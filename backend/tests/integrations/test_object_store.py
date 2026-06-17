"""Object-store port + RemoteWorkspaceHandle host-side logic.

The S3/boto3 wire calls are covered by a live COS round-trip during dev (not in
CI — needs creds); here we pin the vendor-neutral logic with an in-memory
store: key prefixing, the WorkspaceHandle contract, the not-found classifier,
and the COS factory gating.
"""

from __future__ import annotations

import pytest

from valuz_agent.ports.object_store import ObjectInfo, ObjectNotFoundError
from valuz_agent.ports.workspace import RemoteWorkspaceHandle


class _MemStore:
    """In-memory ``ObjectStore`` for testing host-side logic."""

    def __init__(self) -> None:
        self.objs: dict[str, bytes] = {}

    async def put_bytes(self, key: str, data: bytes) -> None:
        self.objs[key] = data

    async def get_bytes(self, key: str) -> bytes:
        if key not in self.objs:
            raise ObjectNotFoundError(key)
        return self.objs[key]

    async def exists(self, key: str) -> bool:
        return key in self.objs

    async def list_keys(self, prefix: str) -> list[ObjectInfo]:
        return [ObjectInfo(k, len(v)) for k, v in self.objs.items() if k.startswith(prefix)]

    async def delete(self, key: str) -> None:
        self.objs.pop(key, None)

    async def delete_prefix(self, prefix: str) -> int:
        keys = [k for k in self.objs if k.startswith(prefix)]
        for k in keys:
            del self.objs[k]
        return len(keys)


async def test_remote_handle_cwd_is_sandbox_path_not_host():
    store = _MemStore()
    h = RemoteWorkspaceHandle(store, key_prefix="projects/demo", sandbox_cwd="/workspace/demo")
    assert str(h.cwd()) == "/workspace/demo"
    assert str(h.subpath("src", "main.py")) == "/workspace/demo/src/main.py"


async def test_remote_handle_io_maps_to_prefixed_keys():
    store = _MemStore()
    h = RemoteWorkspaceHandle(store, key_prefix="projects/demo/", sandbox_cwd="/workspace/demo")
    assert await h.exists("a.txt") is False
    await h.write_bytes("src/main.py", b"print(1)")
    # written under the project prefix, leading slash on rel tolerated
    assert "projects/demo/src/main.py" in store.objs
    assert await h.exists("src/main.py") is True
    assert await h.read_bytes("/src/main.py") == b"print(1)"


async def test_remote_handle_read_missing_raises():
    h = RemoteWorkspaceHandle(_MemStore(), key_prefix="p", sandbox_cwd="/workspace/p")
    with pytest.raises(ObjectNotFoundError):
        await h.read_bytes("nope")


def test_cos_factory_none_when_unconfigured(monkeypatch):
    from valuz_agent.infra.config import settings
    from valuz_agent.integrations.object_store_s3 import cos_object_store

    monkeypatch.setattr(settings, "cos_bucket", None)
    monkeypatch.setattr(settings, "cos_secret_id", None)
    monkeypatch.setattr(settings, "cos_secret_key", None)
    assert cos_object_store() is None


def test_not_found_classifier():
    from valuz_agent.integrations.object_store_s3 import _is_not_found

    class _FakeClientError(Exception):
        def __init__(self, code=None, status=None):
            self.response = {
                "Error": {"Code": code} if code else {},
                "ResponseMetadata": {"HTTPStatusCode": status} if status else {},
            }

    assert _is_not_found(_FakeClientError(code="NoSuchKey")) is True
    assert _is_not_found(_FakeClientError(status=404)) is True
    assert _is_not_found(_FakeClientError(code="AccessDenied")) is False
    assert _is_not_found(ValueError("plain")) is False
