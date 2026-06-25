"""Preflight tests for ``ensure_host_schema_migratable`` (NEVER drops).

The host alembic chain is incremental. The preflight lets a DB stamped at a
*known* revision (or a fresh file) pass through to ``alembic upgrade head``, and
**raises on anything else, deleting nothing** — replacing an earlier probe that
wiped ``valuz_*`` tables on a non-known stamp (which silently destroyed a
downgraded store).
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect, text

from valuz_agent.boot.schema import BASELINE_REVISION, ensure_host_schema_migratable


def _host_tables(engine) -> set[str]:
    return {t for t in inspect(engine).get_table_names() if t.startswith("valuz_")}


def _stamp(engine) -> str | None:
    with engine.connect() as conn:
        row = conn.execute(text("SELECT version_num FROM alembic_version_host")).fetchone()
        return row[0] if row else None


def _create_host_shape(conn, *, stamp: str | None) -> None:
    """A few representative host tables + the version table; ``stamp=None``
    leaves the version table empty (boot died before stamping)."""
    conn.execute(text("CREATE TABLE valuz_agent (id TEXT PRIMARY KEY, user_id TEXT)"))
    conn.execute(text("CREATE TABLE valuz_provider (id TEXT PRIMARY KEY, user_id TEXT)"))
    conn.execute(text("CREATE TABLE alembic_version_host (version_num TEXT PRIMARY KEY)"))
    if stamp is not None:
        conn.execute(text(f"INSERT INTO alembic_version_host VALUES ('{stamp}')"))


# ── safe states: return, nothing touched ──────────────────────────────────


def test_should_pass_when_stamped_at_head(tmp_path) -> None:
    """Stamp == head revision → trust it; nothing raised, data untouched."""
    engine = create_engine(f"sqlite:///{tmp_path / 'on_head.db'}")
    with engine.begin() as conn:
        _create_host_shape(conn, stamp=BASELINE_REVISION)
        conn.execute(text("INSERT INTO valuz_agent VALUES ('a1', 'local-u')"))

    ensure_host_schema_migratable(engine)  # no raise

    assert _stamp(engine) == BASELINE_REVISION
    assert _host_tables(engine) == {"valuz_agent", "valuz_provider"}


def test_should_pass_when_stamped_at_older_known_revision(tmp_path) -> None:
    """A DB on an earlier *known* revision passes through for ``alembic upgrade
    head`` to migrate forward — never wiped. (The baseline file's id is 0002.)"""
    engine = create_engine(f"sqlite:///{tmp_path / 'older_known.db'}")
    with engine.begin() as conn:
        _create_host_shape(conn, stamp="0002")
        conn.execute(text("INSERT INTO valuz_agent VALUES ('a1', 'local-u')"))

    ensure_host_schema_migratable(engine)  # no raise

    assert _stamp(engine) == "0002"
    assert _host_tables(engine) == {"valuz_agent", "valuz_provider"}


def test_should_pass_on_fresh_install(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    ensure_host_schema_migratable(engine)  # no raise
    assert set(inspect(engine).get_table_names()) == set()


# ── unsafe states: raise, NOTHING deleted ─────────────────────────────────


def test_should_raise_and_preserve_when_foreign_stamp_holds_data(tmp_path) -> None:
    """Foreign/unknown stamp WITH data = a downgrade. Raise, delete nothing."""
    engine = create_engine(f"sqlite:///{tmp_path / 'downgrade.db'}")
    with engine.begin() as conn:
        _create_host_shape(conn, stamp="9999_from_the_future")
        conn.execute(text("INSERT INTO valuz_agent VALUES ('a1', 'local-u')"))

    with pytest.raises(RuntimeError, match="not a known revision"):
        ensure_host_schema_migratable(engine)

    assert _host_tables(engine) == {"valuz_agent", "valuz_provider"}
    assert _stamp(engine) == "9999_from_the_future"
    with engine.connect() as conn:
        assert conn.execute(text("SELECT id FROM valuz_agent")).fetchall() == [("a1",)]


def test_should_raise_and_preserve_on_foreign_stamp_with_empty_tables(tmp_path) -> None:
    """Foreign stamp but empty tables → unrecognized state; raise, drop nothing."""
    engine = create_engine(f"sqlite:///{tmp_path / 'foreign_empty.db'}")
    with engine.begin() as conn:
        _create_host_shape(conn, stamp="0099")

    with pytest.raises(RuntimeError, match="unrecognized state"):
        ensure_host_schema_migratable(engine)

    # Never dropped — operator decides (remove the data dir).
    assert _host_tables(engine) == {"valuz_agent", "valuz_provider"}
    assert "alembic_version_host" in set(inspect(engine).get_table_names())


def test_should_raise_when_unstamped_with_tables(tmp_path) -> None:
    """Tables present but version row missing (boot died mid-init) → raise."""
    engine = create_engine(f"sqlite:///{tmp_path / 'no_stamp.db'}")
    with engine.begin() as conn:
        _create_host_shape(conn, stamp=None)

    with pytest.raises(RuntimeError, match="unrecognized state"):
        ensure_host_schema_migratable(engine)

    assert _host_tables(engine) == {"valuz_agent", "valuz_provider"}


def test_should_raise_when_version_table_absent_but_tables_present(tmp_path) -> None:
    """Host tables without any version table (ad-hoc create_all) → raise."""
    engine = create_engine(f"sqlite:///{tmp_path / 'no_vt.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE valuz_agent (id TEXT PRIMARY KEY, user_id TEXT)"))

    with pytest.raises(RuntimeError, match="unrecognized state"):
        ensure_host_schema_migratable(engine)

    assert _host_tables(engine) == {"valuz_agent"}


def test_should_not_delete_anything_including_kernel_when_unmigratable(tmp_path) -> None:
    """The preflight raises BEFORE touching anything — host, kernel, version
    tables all survive (it never drops, so the scope question is moot)."""
    engine = create_engine(f"sqlite:///{tmp_path / 'mixed.db'}")
    with engine.begin() as conn:
        _create_host_shape(conn, stamp="0099")  # foreign, empty
        conn.execute(text("CREATE TABLE sessions (id TEXT PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE alembic_version (version_num TEXT PRIMARY KEY)"))

    with pytest.raises(RuntimeError):
        ensure_host_schema_migratable(engine)

    remaining = set(inspect(engine).get_table_names())
    assert {"valuz_agent", "valuz_provider", "sessions", "alembic_version"} <= remaining
