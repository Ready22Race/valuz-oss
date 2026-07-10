"""Generic ephemeral cache — a ``key → value`` store with optional TTL.

A small, replaceable cache primitive (string values; callers serialise). Used,
for example, for the connector OAuth PKCE handoff (a short-lived ``state →
payload`` entry), but deliberately generic so other transient needs share it.

OSS default is ``FileCache`` (a local-process file store) — right for the
desktop build. A shared multi-client backend has many processes and no shared
filesystem, so the commercial overlay swaps in a Redis-backed cache via
``ext.cache`` (TTL handled natively by Redis).
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path

from valuz_agent.infra.time_utils import now_ms


class CachePort(ABC):
    @abstractmethod
    async def get(self, key: str) -> str | None: ...

    @abstractmethod
    async def set(self, key: str, value: str, *, ttl_seconds: int | None = None) -> None: ...

    @abstractmethod
    async def delete(self, key: str) -> None: ...


class FileCache(CachePort):
    """Filesystem cache — the OSS / desktop default.

    Each key is one JSON file ``{expires_at, value}``; a read past ``expires_at``
    deletes the file and returns ``None`` (``expires_at`` is ``None`` for a
    no-TTL entry). Single-process only — a shared backend uses the Redis cache.
    """

    def __init__(self, base_dir: Path) -> None:
        self._base = base_dir

    def _path(self, key: str) -> Path:
        # Keys are caller-namespaced strings; make the filename safe regardless.
        safe = key.replace("/", "_").replace("\\", "_").replace(":", "_")
        return self._base / f"{safe}.json"

    async def set(self, key: str, value: str, *, ttl_seconds: int | None = None) -> None:
        self._base.mkdir(parents=True, exist_ok=True)
        expires_at = now_ms() + ttl_seconds * 1000 if ttl_seconds is not None else None
        blob = json.dumps({"expires_at": expires_at, "value": value})
        self._path(key).write_text(blob, encoding="utf-8")

    async def get(self, key: str) -> str | None:
        p = self._path(key)
        if not p.is_file():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        expires_at = data.get("expires_at")
        if expires_at is not None and int(expires_at) < now_ms():
            await self.delete(key)
            return None
        value = data.get("value")
        return value if isinstance(value, str) else None

    async def delete(self, key: str) -> None:
        try:
            self._path(key).unlink()
        except OSError:
            pass
