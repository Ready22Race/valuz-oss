"""Tests for the function-style local secret store."""

from __future__ import annotations

from pathlib import Path

from valuz_agent.infra import secret_store


def test_secret_store_exports_file_helpers() -> None:
    assert callable(secret_store.get)
    assert callable(secret_store.put)
    assert callable(secret_store.delete)
    assert callable(secret_store.path)


def test_secret_store_scopes_by_user_id_and_ref(tmp_path: Path, monkeypatch) -> None:
    def _secrets_dir(user_id: str) -> Path:
        path = tmp_path / user_id / "secrets"
        path.mkdir(parents=True, exist_ok=True)
        return path

    monkeypatch.setattr(secret_store.fs_registry, "secrets_dir", _secrets_dir)

    secret_store.put("user-A", "channel/x", "key-A")

    assert secret_store.get("user-A", "channel/x") == "key-A"
    assert secret_store.get("user-B", "channel/x") is None
    assert secret_store.get("user-A", "channel/y") is None
    assert (tmp_path / "user-A" / "secrets" / "channel__x").is_file()


def test_secret_store_delete_is_idempotent(tmp_path: Path, monkeypatch) -> None:
    def _secrets_dir(user_id: str) -> Path:
        path = tmp_path / user_id / "secrets"
        path.mkdir(parents=True, exist_ok=True)
        return path

    monkeypatch.setattr(secret_store.fs_registry, "secrets_dir", _secrets_dir)

    secret_store.put("user-A", "parser/plugin/key", "secret")
    assert secret_store.get("user-A", "parser/plugin/key") == "secret"

    secret_store.delete("user-A", "parser/plugin/key")
    secret_store.delete("user-A", "parser/plugin/key")

    assert secret_store.get("user-A", "parser/plugin/key") is None
