"""Tests for database URL runtime resolution."""

from __future__ import annotations

from pathlib import Path

from valuz_agent.infra import db_urls, fs_registry
from valuz_agent.infra.config import Settings


def _patch_settings(monkeypatch, s: Settings) -> None:
    monkeypatch.setattr(db_urls, "settings", s)
    monkeypatch.setattr(fs_registry, "settings", s)


class TestDatabaseUrlConfig:
    def test_default_sqlite_uses_configured_data_root(self, monkeypatch) -> None:
        s = Settings(data_dir="/tmp/valuz-test-db", deployment_type="cloud")
        _patch_settings(monkeypatch, s)
        monkeypatch.setattr(db_urls, "_local_user_id", lambda: "user-A")

        assert db_urls.is_sqlite_runtime() is True
        assert db_urls.db_url() == "sqlite:////tmp/valuz-test-db/valuz.db"
        assert db_urls.sqlite_path_from_url(db_urls.db_url()) == Path(
            "/tmp/valuz-test-db/valuz.db"
        )
        assert (
            db_urls.db_url_async()
            == "sqlite+aiosqlite:////tmp/valuz-test-db/valuz.db"
        )
        assert db_urls.sqlite_path_from_url(db_urls.db_url_async()) == Path(
            "/tmp/valuz-test-db/valuz.db"
        )

    def test_default_sqlite_expands_user_placeholder(self, monkeypatch) -> None:
        s = Settings(data_dir="/tmp/valuz-test-db/{user_id}", deployment_type="cloud")
        _patch_settings(monkeypatch, s)
        monkeypatch.setattr(db_urls, "_local_user_id", lambda: "user-A")

        assert db_urls.db_url() == "sqlite:////tmp/valuz-test-db/user-A/valuz.db"
        assert db_urls.sqlite_path_from_url(db_urls.db_url()) == Path(
            "/tmp/valuz-test-db/user-A/valuz.db"
        )

    def test_explicit_pg_url(self, monkeypatch) -> None:
        s = Settings(
            data_dir="/tmp/valuz-test-db",
            database_url="postgresql://valuz:valuz@localhost:5432/valuz",
        )
        monkeypatch.setattr(db_urls, "settings", s)

        assert db_urls.is_sqlite_runtime() is False
        assert db_urls.sqlite_path_from_url(db_urls.db_url()) is None
        assert db_urls.db_url() == "postgresql://valuz:valuz@localhost:5432/valuz"
        assert (
            db_urls.db_url_async()
            == "postgresql+asyncpg://valuz:valuz@localhost:5432/valuz"
        )

    def test_explicit_sqlite_url(self, monkeypatch) -> None:
        s = Settings(
            data_dir="/tmp/valuz-test-db",
            database_url="sqlite:///custom.db",
        )
        monkeypatch.setattr(db_urls, "settings", s)

        assert db_urls.is_sqlite_runtime() is True
        assert db_urls.db_url() == "sqlite:///custom.db"
        assert db_urls.sqlite_path_from_url(db_urls.db_url()) == Path("custom.db")
        assert db_urls.db_url_async() == "sqlite+aiosqlite:///custom.db"
        assert db_urls.sqlite_path_from_url(db_urls.db_url_async()) == Path("custom.db")

    def test_unknown_url_scheme_is_left_unchanged_for_async(self, monkeypatch) -> None:
        s = Settings(data_dir="/tmp/valuz-test-db", database_url="mysql://x")
        monkeypatch.setattr(db_urls, "settings", s)

        assert db_urls.db_url_async() == "mysql://x"


class TestKernelDbUrlConfig:
    def test_default_kernel_db_is_separate_file(self, monkeypatch) -> None:
        """No override: the kernel gets its OWN kernel.db, distinct from valuz.db."""
        s = Settings(data_dir="/tmp/valuz-test-db", deployment_type="cloud")
        _patch_settings(monkeypatch, s)
        monkeypatch.setattr(db_urls, "_local_user_id", lambda: "user-A")

        assert db_urls.kernel_db_url() == "sqlite:////tmp/valuz-test-db/kernel.db"
        assert db_urls.sqlite_path_from_url(db_urls.kernel_db_url()) == Path(
            "/tmp/valuz-test-db/kernel.db"
        )
        assert (
            db_urls.kernel_db_url_async()
            == "sqlite+aiosqlite:////tmp/valuz-test-db/kernel.db"
        )
        assert db_urls.kernel_db_url() != db_urls.db_url()  # the split

    def test_explicit_database_url_colocates_kernel(self, monkeypatch) -> None:
        """An explicit host DB (e.g. Postgres) shares the store with the kernel."""
        s = Settings(
            data_dir="/tmp/valuz-test-db",
            database_url="postgresql://valuz:valuz@localhost:5432/valuz",
        )
        monkeypatch.setattr(db_urls, "settings", s)

        assert db_urls.sqlite_path_from_url(db_urls.kernel_db_url()) is None
        assert db_urls.kernel_db_url() == db_urls.db_url()
        assert db_urls.kernel_db_url_async() == db_urls.db_url_async()

    def test_explicit_kernel_database_url_wins(self, monkeypatch) -> None:
        s = Settings(
            data_dir="/tmp/valuz-test-db",
            database_url="postgresql://valuz:valuz@localhost:5432/valuz",
            kernel_database_url="sqlite:///kernel-only.db",
        )
        monkeypatch.setattr(db_urls, "settings", s)

        assert db_urls.kernel_db_url() == "sqlite:///kernel-only.db"
        assert db_urls.sqlite_path_from_url(db_urls.kernel_db_url()) == Path(
            "kernel-only.db"
        )
        assert db_urls.kernel_db_url_async() == "sqlite+aiosqlite:///kernel-only.db"
