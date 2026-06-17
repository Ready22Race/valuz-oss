"""Port: object storage — the ⑤ materials backend for a cloud sandbox.

When the kernel runs in a cloud sandbox (AGS), host and kernel no longer share
a filesystem. Project files move through an **object store** the sandbox mounts
as its workspace (the design's ``ObjectStoreSource`` /
``docs/design/kernel-sandbox-deployment.md`` §3.7.1): the host writes the
project into the bucket under a per-project prefix, AGS mounts that bucket so
the kernel sees it as ``/workspace``, and results land back in the bucket for
the host to read.

This protocol is deliberately vendor-neutral — Tencent COS, AWS S3, Aliyun OSS
and MinIO are all S3-compatible and bind to the same ``S3ObjectStore``
implementation; swapping providers is changing the endpoint, not the business
layer. Keys are POSIX-ish ``a/b/c.txt`` strings relative to the bucket root.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ObjectInfo:
    """One stored object — its key and size in bytes."""

    key: str
    size: int


class ObjectStore(Protocol):
    """Async key→bytes store scoped to a single bucket.

    Implementations wrap a blocking SDK off the event loop; callers ``await``
    every operation. All keys are bucket-relative (no leading slash).
    """

    async def put_bytes(self, key: str, data: bytes) -> None:
        """Write ``data`` at ``key`` (overwrites)."""
        ...

    async def get_bytes(self, key: str) -> bytes:
        """Read ``key``. Raises ``ObjectNotFoundError`` if absent."""
        ...

    async def exists(self, key: str) -> bool:
        """True iff an object exists at ``key``."""
        ...

    async def list_keys(self, prefix: str) -> list[ObjectInfo]:
        """Every object whose key starts with ``prefix`` (recursive)."""
        ...

    async def delete(self, key: str) -> None:
        """Remove ``key``. Idempotent — absent key is a no-op."""
        ...

    async def delete_prefix(self, prefix: str) -> int:
        """Remove every object under ``prefix``; return the count deleted."""
        ...


class ObjectStoreError(RuntimeError):
    """An object-store operation failed."""


class ObjectNotFoundError(ObjectStoreError):
    """No object exists at the requested key."""
