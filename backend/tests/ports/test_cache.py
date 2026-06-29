"""``FileCache`` — the OSS / desktop default ephemeral cache."""

from __future__ import annotations

import pytest

import valuz_agent.ports.cache as mod
from valuz_agent.ports.cache import FileCache


@pytest.mark.asyncio
async def test_set_get_delete_roundtrip(tmp_path) -> None:
    cache = FileCache(tmp_path / "cache")
    await cache.set("k1", "value-1", ttl_seconds=600)
    assert await cache.get("k1") == "value-1"

    await cache.delete("k1")
    assert await cache.get("k1") is None


@pytest.mark.asyncio
async def test_missing_key_returns_none(tmp_path) -> None:
    cache = FileCache(tmp_path / "cache")
    assert await cache.get("nope") is None
    # delete of a missing key is a no-op (no error).
    await cache.delete("nope")


@pytest.mark.asyncio
async def test_no_ttl_never_expires(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(mod, "now_ms", lambda: 1_000)
    cache = FileCache(tmp_path / "cache")
    await cache.set("k1", "forever")  # no ttl_seconds
    monkeypatch.setattr(mod, "now_ms", lambda: 10**15)
    assert await cache.get("k1") == "forever"


@pytest.mark.asyncio
async def test_expired_key_is_evicted(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(mod, "now_ms", lambda: 1_000)
    cache = FileCache(tmp_path / "cache")
    await cache.set("k1", "value", ttl_seconds=10)  # expires_at = 11_000

    monkeypatch.setattr(mod, "now_ms", lambda: 999_999)  # well past expiry
    assert await cache.get("k1") is None
    # The stale file is cleaned up on the expired read.
    assert not (tmp_path / "cache" / "k1.json").exists()
