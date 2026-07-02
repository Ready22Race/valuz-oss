"""User-scoped local secret file helpers.

This module is intentionally not a port or an injectable store. The runtime
always writes local files; deployment decides whether that local path is a real
disk, CFS, JuiceFS, or another mounted filesystem.
"""

from __future__ import annotations

from pathlib import Path

from valuz_agent.infra.fs_registry import fs_registry


def safe_ref(ref: str) -> str:
    return ref.replace("/", "__").replace("\\", "__")


def path(user_id: str, ref: str) -> Path:
    return fs_registry.secrets_dir(user_id) / safe_ref(ref)


def get(user_id: str, ref: str) -> str | None:
    secret_path = path(user_id, ref)
    if not secret_path.is_file():
        return None
    return secret_path.read_text(encoding="utf-8").strip()


def put(user_id: str, ref: str, value: str) -> None:
    secret_path = path(user_id, ref)
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    secret_path.write_text(value, encoding="utf-8")


def delete(user_id: str, ref: str) -> None:
    secret_path = path(user_id, ref)
    if secret_path.is_file():
        secret_path.unlink()


__all__ = ["delete", "get", "path", "put", "safe_ref"]
