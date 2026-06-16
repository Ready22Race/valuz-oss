"""Tests for the one-time kernel-store split out of the shared valuz.db.

Builds real SQLite files and drives ``migrate_kernel_store_out_of_host_db``
directly (it reads ``settings.db_url`` / ``settings.kernel_db_url``), asserting
the happy path, idempotency, re-entrancy after an interrupted run, and that a
verification mismatch aborts WITHOUT dropping the source.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import valuz_agent.boot.kernel_db_split as split


def _tables(path: Path) -> set[str]:
    with sqlite3.connect(path) as conn:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _count(path: Path, table: str) -> int:
    with sqlite3.connect(path) as conn:
        return int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def _make_host_db(path: Path, *, sessions: int = 3, with_checkpoints: bool = False) -> None:
    """A pre-split valuz.db: kernel trio + kernel/host alembic stamps + a host table."""
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, name TEXT)")
        conn.execute("CREATE TABLE messages (id TEXT PRIMARY KEY, session_id TEXT)")
        conn.execute("CREATE TABLE events (seq INTEGER PRIMARY KEY, session_id TEXT)")
        conn.execute("CREATE INDEX ix_events_session ON events (session_id)")
        for i in range(sessions):
            conn.execute("INSERT INTO sessions VALUES (?, ?)", (f"s{i}", f"name{i}"))
            conn.execute("INSERT INTO messages VALUES (?, ?)", (f"m{i}", f"s{i}"))
            conn.execute("INSERT INTO events VALUES (?, ?)", (i, f"s{i}"))
        if with_checkpoints:
            conn.execute("CREATE TABLE checkpoints (thread_id TEXT, cp TEXT)")
            conn.execute("INSERT INTO checkpoints VALUES ('t1', 'blob')")
        # Kernel stamp (alembic_version) + host stamp (alembic_version_host) + a host table.
        conn.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)")
        conn.execute("INSERT INTO alembic_version VALUES ('0001')")
        conn.execute("CREATE TABLE alembic_version_host (version_num VARCHAR(32) PRIMARY KEY)")
        conn.execute("INSERT INTO alembic_version_host VALUES ('0003')")
        conn.execute("CREATE TABLE valuz_thing (id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO valuz_thing VALUES ('keep-me')")
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def split_paths(tmp_path, monkeypatch):
    """Point settings at tmp host/kernel SQLite files."""
    host = tmp_path / "valuz.db"
    kernel = tmp_path / "kernel.db"
    monkeypatch.setattr(split.settings, "data_dir", tmp_path)
    monkeypatch.setattr(split.settings, "database_url", None)
    monkeypatch.setattr(split.settings, "kernel_database_url", f"sqlite:///{kernel}")
    return host, kernel


def test_happy_path_moves_kernel_tables_and_drops_originals(split_paths) -> None:
    host, kernel = split_paths
    _make_host_db(host, sessions=5, with_checkpoints=True)

    split.migrate_kernel_store_out_of_host_db()

    # Kernel file now carries the kernel tables + stamp, at the right counts.
    assert {"sessions", "messages", "events", "checkpoints"} <= _tables(kernel)
    assert _count(kernel, "sessions") == 5
    assert _count(kernel, "messages") == 5
    assert _count(kernel, "events") == 5
    assert _count(kernel, "checkpoints") == 1
    with sqlite3.connect(kernel) as conn:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0001"
    # The copied index came across too.
    assert "ix_events_session" in {
        r[0]
        for r in sqlite3.connect(kernel).execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )
    }

    # Host file dropped the kernel tables + kernel stamp, KEPT host concerns.
    host_tables = _tables(host)
    assert not ({"sessions", "messages", "events", "checkpoints"} & host_tables)
    assert "alembic_version" not in host_tables
    assert "alembic_version_host" in host_tables
    assert "valuz_thing" in host_tables
    assert _count(host, "valuz_thing") == 1

    # Backup exists and still holds the original kernel data.
    backup = host.with_name(host.name + split._BACKUP_SUFFIX)
    assert backup.exists()
    assert _count(backup, "sessions") == 5


def test_noop_when_already_split(split_paths) -> None:
    host, kernel = split_paths
    # Host file with NO kernel tables (already migrated / fresh host).
    conn = sqlite3.connect(host)
    conn.execute("CREATE TABLE valuz_thing (id TEXT PRIMARY KEY)")
    conn.execute("CREATE TABLE alembic_version_host (version_num VARCHAR(32))")
    conn.commit()
    conn.close()

    split.migrate_kernel_store_out_of_host_db()

    # No backup created, host untouched, no kernel.db spun up with data.
    assert not host.with_name(host.name + split._BACKUP_SUFFIX).exists()
    assert _tables(host) == {"valuz_thing", "alembic_version_host"}


def test_noop_on_fresh_install(split_paths) -> None:
    host, _kernel = split_paths
    # Neither file exists yet.
    split.migrate_kernel_store_out_of_host_db()  # must not raise


def test_reentrant_after_partial_copy(split_paths) -> None:
    host, kernel = split_paths
    _make_host_db(host, sessions=4)
    # Simulate an interrupted run: kernel.db already has the schema + SOME rows.
    conn = sqlite3.connect(kernel)
    conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO sessions VALUES ('s0', 'name0')")
    conn.commit()
    conn.close()

    split.migrate_kernel_store_out_of_host_db()

    # OR IGNORE filled the gap to the full set; verify passed; originals dropped.
    assert _count(kernel, "sessions") == 4
    assert "sessions" not in _tables(host)


def test_verification_mismatch_aborts_without_dropping(split_paths) -> None:
    host, kernel = split_paths
    _make_host_db(host, sessions=3)
    # Pre-seed kernel.db with an EXTRA session not in the source so the post-copy
    # count (4) won't match the source (3) -> verification must raise.
    conn = sqlite3.connect(kernel)
    conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO sessions VALUES ('extra', 'x')")
    conn.commit()
    conn.close()

    with pytest.raises(RuntimeError, match="verification failed"):
        split.migrate_kernel_store_out_of_host_db()

    # Source is intact (NOT dropped) and the backup was taken.
    assert "sessions" in _tables(host)
    assert _count(host, "sessions") == 3
    assert host.with_name(host.name + split._BACKUP_SUFFIX).exists()
