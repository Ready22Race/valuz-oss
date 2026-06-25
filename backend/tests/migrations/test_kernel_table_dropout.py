"""Preflight tests for ``ensure_kernel_schema_migratable`` (NEVER drops).

Mirrors ``test_host_baseline_reset`` for the kernel. A DB stamped at a *known*
revision (or a fresh file with no kernel tables) passes through to ``alembic
upgrade head``; any other state raises and leaves every kernel table + row
intact. The probe never deletes — it replaced one that wiped the trio on a
non-known stamp.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect, text

from valuz_agent.boot.kernel import _known_kernel_revisions, ensure_kernel_schema_migratable

_TRIO = {"sessions", "messages", "events"}


def _tables(engine) -> set[str]:
    return set(inspect(engine).get_table_names())


def _a_known_revision() -> str:
    """Any revision id currently in the kernel chain (just ``0001`` today)."""
    return sorted(_known_kernel_revisions())[0]


def _create_kernel_trio(conn, *, stamp: str | None) -> None:
    """The kernel trio + the default ``alembic_version`` table; ``stamp=None``
    leaves the version table empty (boot died before stamping)."""
    conn.execute(text("CREATE TABLE sessions (id TEXT PRIMARY KEY, user_id TEXT)"))
    conn.execute(text("CREATE TABLE messages (id TEXT PRIMARY KEY, user_id TEXT)"))
    conn.execute(text("CREATE TABLE events (id INTEGER PRIMARY KEY, user_id TEXT)"))
    conn.execute(text("CREATE TABLE alembic_version (version_num TEXT PRIMARY KEY)"))
    if stamp is not None:
        conn.execute(text(f"INSERT INTO alembic_version VALUES ('{stamp}')"))


# ── safe states: return, nothing touched ──────────────────────────────────


def test_should_pass_when_stamped_at_known_revision(tmp_path) -> None:
    """Stamp on a known revision → trust it; trio + data untouched, no raise."""
    engine = create_engine(f"sqlite:///{tmp_path / 'known.db'}")
    with engine.begin() as conn:
        _create_kernel_trio(conn, stamp=_a_known_revision())
        conn.execute(text("INSERT INTO sessions VALUES ('s1', 'u1')"))

    ensure_kernel_schema_migratable(engine)  # no raise

    assert _TRIO <= _tables(engine)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT id FROM sessions")).fetchall() == [("s1",)]


def test_should_pass_on_fresh_install(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    ensure_kernel_schema_migratable(engine)  # no raise
    assert _tables(engine) == set()


# ── unsafe states: raise, NOTHING deleted ─────────────────────────────────


def test_should_raise_and_preserve_when_foreign_stamp_holds_data(tmp_path) -> None:
    """A foreign/unknown kernel stamp WITH real session data = a downgrade.
    Refuse to start and DO NOT wipe — the kernel store stays intact."""
    engine = create_engine(f"sqlite:///{tmp_path / 'downgrade.db'}")
    with engine.begin() as conn:
        _create_kernel_trio(conn, stamp="9999_from_the_future")
        conn.execute(text("INSERT INTO sessions VALUES ('s1', 'u1')"))

    with pytest.raises(RuntimeError, match="not a known revision"):
        ensure_kernel_schema_migratable(engine)

    assert _TRIO <= _tables(engine)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT id FROM sessions")).fetchall() == [("s1",)]


def test_should_raise_and_preserve_on_foreign_stamp_with_empty_trio(tmp_path) -> None:
    """Foreign stamp, empty trio → unrecognized state; raise, drop nothing."""
    engine = create_engine(f"sqlite:///{tmp_path / 'foreign_empty.db'}")
    with engine.begin() as conn:
        _create_kernel_trio(conn, stamp="0099")

    with pytest.raises(RuntimeError, match="unrecognized state"):
        ensure_kernel_schema_migratable(engine)

    assert _TRIO <= _tables(engine)


def test_should_raise_on_torn_half_created_trio(tmp_path) -> None:
    """An interrupted first boot left a partial trio and no stamp → raise, keep."""
    engine = create_engine(f"sqlite:///{tmp_path / 'torn.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE sessions (id TEXT PRIMARY KEY)"))

    with pytest.raises(RuntimeError, match="unrecognized state"):
        ensure_kernel_schema_migratable(engine)

    assert "sessions" in _tables(engine)


def test_should_not_delete_host_or_checkpoint_tables_when_unmigratable(tmp_path) -> None:
    """Raises before touching anything: the trio, host ``valuz_*`` tables, the
    host stamp, and langgraph checkpoint tables all survive."""
    engine = create_engine(f"sqlite:///{tmp_path / 'mixed.db'}")
    with engine.begin() as conn:
        _create_kernel_trio(conn, stamp="0099")  # foreign, empty
        conn.execute(text("CREATE TABLE valuz_agent (id TEXT PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE alembic_version_host (version_num TEXT PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE checkpoints (thread_id TEXT PRIMARY KEY)"))

    with pytest.raises(RuntimeError):
        ensure_kernel_schema_migratable(engine)

    remaining = _tables(engine)
    assert _TRIO <= remaining
    assert {"valuz_agent", "alembic_version_host", "checkpoints"} <= remaining
