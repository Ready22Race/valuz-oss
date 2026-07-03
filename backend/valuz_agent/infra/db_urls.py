"""Runtime database URL resolution.

``Settings`` only describes configured values. The local SQLite default is a
filesystem layout decision, so it resolves through ``FsRegistry`` after the
local owner id is known.
"""

from __future__ import annotations

from pathlib import Path

from valuz_agent.infra.config import settings
from valuz_agent.infra.fs_registry import fs_registry


def _to_async_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("sqlite://"):
        return url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return url


def sqlite_path_from_url(url: str) -> Path | None:
    prefixes = ("sqlite+aiosqlite:///", "sqlite:///")
    prefix = next((p for p in prefixes if url.startswith(p)), None)
    if prefix is None:
        return None
    raw = url.removeprefix(prefix)
    if not raw or raw == ":memory:":
        return None
    if url.startswith(prefix + "/"):
        raw = "/" + raw.lstrip("/")
    return Path(raw)


def _local_user_id() -> str:
    from valuz_agent.infra.local_identity import resolve_local_user_id

    return resolve_local_user_id()


def db_url() -> str:
    if settings.database_url:
        return settings.database_url
    return fs_registry.db_url(_local_user_id())


def db_url_async() -> str:
    if settings.database_url:
        return _to_async_url(settings.database_url)
    return fs_registry.db_url_async(_local_user_id())


def kernel_db_url() -> str:
    if settings.kernel_database_url:
        return settings.kernel_database_url
    if settings.database_url:
        return settings.database_url
    return fs_registry.kernel_db_url(_local_user_id())


def kernel_db_url_async() -> str:
    if settings.kernel_database_url:
        return _to_async_url(settings.kernel_database_url)
    if settings.database_url:
        return _to_async_url(settings.database_url)
    return fs_registry.kernel_db_url_async(_local_user_id())


def is_sqlite_runtime() -> bool:
    return db_url().startswith("sqlite")


__all__ = [
    "db_url",
    "db_url_async",
    "is_sqlite_runtime",
    "kernel_db_url",
    "kernel_db_url_async",
    "sqlite_path_from_url",
]
