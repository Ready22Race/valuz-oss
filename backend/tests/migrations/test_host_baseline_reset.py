"""Self-heal tests for ``drop_stale_host_tables`` (incremental host chain).

The host alembic chain is incremental: the baseline creates the schema and
later revisions ALTER it. The probe is data-preserving — it keeps any DB
stamped at a *known* revision (``alembic upgrade head`` migrates it forward)
and only drops + rebuilds when the ``alembic_version_host`` stamp is
unknown/foreign, or host tables are present with no stamp at all (a boot that
died mid-initialization).
"""

from __future__ import annotations

from sqlalchemy import create_engine, inspect, text

from valuz_agent.boot.schema import BASELINE_REVISION, drop_stale_host_tables


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


def test_should_noop_when_stamped_at_head(tmp_path) -> None:
    """Stamp == head revision → trust it; tables and data untouched."""
    engine = create_engine(f"sqlite:///{tmp_path / 'on_head.db'}")
    with engine.begin() as conn:
        _create_host_shape(conn, stamp=BASELINE_REVISION)
        conn.execute(text("INSERT INTO valuz_agent VALUES ('a1', 'local-u')"))

    drop_stale_host_tables(engine)

    assert _stamp(engine) == BASELINE_REVISION
    assert _host_tables(engine) == {"valuz_agent", "valuz_provider"}
    with engine.connect() as conn:
        assert conn.execute(text("SELECT id FROM valuz_agent")).fetchall() == [("a1",)]


def test_should_preserve_when_stamped_at_older_known_revision(tmp_path) -> None:
    """A DB on an earlier *known* revision is left for ``alembic upgrade head``
    to migrate forward — data-preserving, not wiped. (The baseline file's
    revision id is ``0002``.)"""
    engine = create_engine(f"sqlite:///{tmp_path / 'older_known.db'}")
    with engine.begin() as conn:
        _create_host_shape(conn, stamp="0002")
        conn.execute(text("INSERT INTO valuz_agent VALUES ('a1', 'local-u')"))

    drop_stale_host_tables(engine)

    assert _stamp(engine) == "0002"
    assert _host_tables(engine) == {"valuz_agent", "valuz_provider"}
    with engine.connect() as conn:
        assert conn.execute(text("SELECT id FROM valuz_agent")).fetchall() == [("a1",)]


def test_should_reset_when_stamped_by_foreign_revision(tmp_path) -> None:
    """An unknown/foreign stamp (a retired id like ``0001``, or one from a
    diverged branch) → full reset."""
    for foreign in ("0001", "0099", "deadbeef"):
        engine = create_engine(f"sqlite:///{tmp_path / f'foreign_{foreign}.db'}")
        with engine.begin() as conn:
            _create_host_shape(conn, stamp=foreign)

        drop_stale_host_tables(engine)

        assert _host_tables(engine) == set()
        assert "alembic_version_host" not in set(inspect(engine).get_table_names())


def test_should_reset_when_stamp_row_is_missing(tmp_path) -> None:
    """Tables exist but the version table is empty (boot died before the
    stamp landed) → unknown provenance, reset."""
    engine = create_engine(f"sqlite:///{tmp_path / 'no_stamp.db'}")
    with engine.begin() as conn:
        _create_host_shape(conn, stamp=None)

    drop_stale_host_tables(engine)

    assert _host_tables(engine) == set()
    assert "alembic_version_host" not in set(inspect(engine).get_table_names())


def test_should_reset_when_version_table_is_absent(tmp_path) -> None:
    """Host tables without any version table (e.g. ad-hoc create_all) → reset."""
    engine = create_engine(f"sqlite:///{tmp_path / 'no_vt.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE valuz_agent (id TEXT PRIMARY KEY, user_id TEXT)"))

    drop_stale_host_tables(engine)

    assert _host_tables(engine) == set()


def test_should_not_touch_kernel_tables_on_reset(tmp_path) -> None:
    """The reset is host-scoped: kernel tables (sessions/messages/events)
    survive — the kernel chain owns its own lifecycle."""
    engine = create_engine(f"sqlite:///{tmp_path / 'mixed.db'}")
    with engine.begin() as conn:
        # A foreign/unknown stamp triggers the host reset (any non-chain id).
        _create_host_shape(conn, stamp="0099")
        conn.execute(text("CREATE TABLE sessions (id TEXT PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE alembic_version (version_num TEXT PRIMARY KEY)"))

    drop_stale_host_tables(engine)

    remaining = set(inspect(engine).get_table_names())
    assert _host_tables(engine) == set()
    assert {"sessions", "alembic_version"} <= remaining


def test_should_noop_on_fresh_install(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    drop_stale_host_tables(engine)
    assert set(inspect(engine).get_table_names()) == set()
