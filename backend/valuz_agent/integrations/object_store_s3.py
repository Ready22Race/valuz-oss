"""S3-compatible ``ObjectStore`` — backs the cloud-sandbox workspace.

One implementation serves every S3-compatible provider (Tencent COS, AWS S3,
Aliyun OSS, MinIO); the provider is just a different endpoint + addressing
style. ``cos_object_store`` is the Tencent COS factory (virtual-hosted-style +
SigV4, which COS requires — path-style is rejected with
``PathStyleDomainForbidden``).

``boto3`` is synchronous; every call runs in a worker thread
(``asyncio.to_thread``) so the event loop never blocks — the same discipline
the host applies to DB access. ``boto3`` is an optional dependency (extra
``ags``); this module imports it lazily so OSS installs without it pay nothing.
"""

from __future__ import annotations

import asyncio
import logging

from valuz_agent.ports.object_store import (
    ObjectInfo,
    ObjectNotFoundError,
    ObjectStore,
    ObjectStoreError,
)

logger = logging.getLogger("valuz_agent.sandbox")


class S3ObjectStore:
    """``ObjectStore`` over any S3-compatible bucket via boto3."""

    def __init__(
        self,
        *,
        bucket: str,
        endpoint_url: str,
        region: str,
        access_key: str,
        secret_key: str,
        addressing_style: str = "virtual",
    ) -> None:
        import boto3
        from botocore.client import Config

        self._bucket = bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": addressing_style},
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        )

    async def put_bytes(self, key: str, data: bytes) -> None:
        try:
            await asyncio.to_thread(
                self._client.put_object, Bucket=self._bucket, Key=key, Body=data
            )
        except Exception as exc:  # noqa: BLE001 — surface as a typed store error
            raise ObjectStoreError(f"put {key} failed: {exc}") from exc

    async def get_bytes(self, key: str) -> bytes:
        try:
            resp = await asyncio.to_thread(
                self._client.get_object, Bucket=self._bucket, Key=key
            )
            return await asyncio.to_thread(resp["Body"].read)
        except Exception as exc:  # noqa: BLE001
            if _is_not_found(exc):
                raise ObjectNotFoundError(key) from exc
            raise ObjectStoreError(f"get {key} failed: {exc}") from exc

    async def exists(self, key: str) -> bool:
        try:
            await asyncio.to_thread(
                self._client.head_object, Bucket=self._bucket, Key=key
            )
            return True
        except Exception as exc:  # noqa: BLE001
            if _is_not_found(exc):
                return False
            raise ObjectStoreError(f"head {key} failed: {exc}") from exc

    async def list_keys(self, prefix: str) -> list[ObjectInfo]:
        out: list[ObjectInfo] = []
        token: str | None = None
        try:
            while True:
                kwargs = {"Bucket": self._bucket, "Prefix": prefix}
                if token:
                    kwargs["ContinuationToken"] = token
                resp = await asyncio.to_thread(self._client.list_objects_v2, **kwargs)
                for obj in resp.get("Contents", []):
                    out.append(ObjectInfo(key=obj["Key"], size=int(obj.get("Size", 0))))
                if not resp.get("IsTruncated"):
                    break
                token = resp.get("NextContinuationToken")
        except Exception as exc:  # noqa: BLE001
            raise ObjectStoreError(f"list {prefix} failed: {exc}") from exc
        return out

    async def delete(self, key: str) -> None:
        try:
            await asyncio.to_thread(
                self._client.delete_object, Bucket=self._bucket, Key=key
            )
        except Exception as exc:  # noqa: BLE001
            raise ObjectStoreError(f"delete {key} failed: {exc}") from exc

    async def delete_prefix(self, prefix: str) -> int:
        infos = await self.list_keys(prefix)
        if not infos:
            return 0
        # Per-key deletes (bounded concurrency), NOT the batch DeleteObjects:
        # COS rejects batch delete without a ``Content-MD5`` header that boto3
        # no longer auto-adds — single deletes avoid that S3-compat quirk and
        # work uniformly across providers.
        deleted = 0
        for i in range(0, len(infos), 64):
            chunk = infos[i : i + 64]
            await asyncio.gather(*(self.delete(o.key) for o in chunk))
            deleted += len(chunk)
        return deleted


async def stage_directory(
    store: ObjectStore,
    prefix: str,
    host_dir: str,
    *,
    max_files: int,
    max_bytes: int,
) -> tuple[int, int]:
    """Upload every file under ``host_dir`` to ``{prefix}/<relpath>`` in
    ``store``. Returns ``(file_count, total_bytes)``. Skips symlinks; stops with
    a loud log (never silent truncation) when the file/byte caps are hit. Shared
    by the AGS per-project stage-in and the user-scoped COS sync."""
    import os

    count = 0
    total = 0
    for root, _dirs, files in os.walk(host_dir):
        for name in files:
            fpath = os.path.join(root, name)
            if os.path.islink(fpath):
                continue
            try:
                data = await asyncio.to_thread(_read_file_bytes, fpath)
            except OSError:
                logger.warning("stage: unreadable, skipped %s", fpath, exc_info=True)
                continue
            if count >= max_files or total + len(data) > max_bytes:
                logger.warning(
                    "stage: cap hit at %d files / %d bytes staging %s → %s — "
                    "REMAINING FILES NOT UPLOADED.",
                    count,
                    total,
                    host_dir,
                    prefix,
                )
                return count, total
            rel = os.path.relpath(fpath, host_dir).replace(os.sep, "/")
            await store.put_bytes(f"{prefix}/{rel}", data)
            count += 1
            total += len(data)
    return count, total


def _read_file_bytes(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def _is_not_found(exc: Exception) -> bool:
    """True for an S3 404 / NoSuchKey regardless of SDK error class."""
    resp = getattr(exc, "response", None)
    if not isinstance(resp, dict):
        return False
    code = resp.get("Error", {}).get("Code")
    status = resp.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return code in ("NoSuchKey", "NotFound", "404") or status == 404


def cos_object_store() -> ObjectStore | None:
    """Build a COS-backed ``ObjectStore`` from ``VALUZ_COS_*`` settings, or
    ``None`` when COS isn't configured (bucket / creds absent)."""
    from valuz_agent.infra.config import settings

    if not (settings.cos_bucket and settings.cos_secret_id and settings.cos_secret_key):
        return None
    endpoint = settings.cos_endpoint or f"https://cos.{settings.cos_region}.myqcloud.com"
    return S3ObjectStore(
        bucket=settings.cos_bucket,
        endpoint_url=endpoint,
        region=settings.cos_region,
        access_key=settings.cos_secret_id,
        secret_key=settings.cos_secret_key,
    )
