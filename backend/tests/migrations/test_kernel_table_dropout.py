"""Self-heal tests for ``drop_stale_kernel_tables`` (incremental kernel chain).

Mirrors ``test_host_baseline_reset`` for the kernel. The kernel alembic chain is
incremental and the probe is data-preserving: a DB stamped at a *known* revision
is migrated forward by ``alembic upgrade head`` (never dropped). Only an
unknown/foreign stamp, or kernel tables present with no stamp at all (a boot
that died mid-initialization, or a half-created trio), triggers a
drop-and-rebuild of the kernel-owned tables. Host ``valuz_*`` tables and the
DeepAgents langgraph checkpoint tables in the same file are never touched.
"""

from __future__ import annotations

from sqlalchemy import create_engine, inspect, text

from valuz_agent.boot.kernel import _known_kernel_revisions, drop_stale_kernel_tables

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


def test_should_noop_when_stamped_at_known_revision(tmp_path) -> None:
    """Stamp on a known revision → trust it; trio and data untouched."""
    engine = create_engine(f"sqlite:///{tmp_path / 'known.db'}")
    with engine.begin() as conn:
        _create_kernel_trio(conn, stamp=_a_known_revision())
        conn.execute(text("INSERT INTO sessions VALUES ('s1', 'u1')"))

    drop_stale_kernel_tables(engine)

    assert _TRIO <= _tables(engine)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT id FROM sessions")).fetchall() == [("s1",)]


def test_should_reset_when_stamped_by_foreign_revision(tmp_path) -> None:
    """An unknown/foreign stamp (diverged branch, corruption) → reset."""
    for foreign in ("0099", "deadbeef"):
        engine = create_engine(f"sqlite:///{tmp_path / f'foreign_{foreign}.db'}")
        with engine.begin() as conn:
            _create_kernel_trio(conn, stamp=foreign)

        drop_stale_kernel_tables(engine)

        remaining = _tables(engine)
        assert not (_TRIO & remaining)
        assert "alembic_version" not in remaining


def test_should_reset_when_stamp_row_is_missing(tmp_path) -> None:
    """Trio exists but the version table is empty (boot died before the stamp
    landed) → unknown provenance, reset."""
    engine = create_engine(f"sqlite:///{tmp_path / 'no_stamp.db'}")
    with engine.begin() as conn:
        _create_kernel_trio(conn, stamp=None)

    drop_stale_kernel_tables(engine)

    assert not (_TRIO & _tables(engine))


def test_should_reset_torn_half_created_trio(tmp_path) -> None:
    """An interrupted first boot left a partial trio and no stamp → reset."""
    engine = create_engine(f"sqlite:///{tmp_path / 'torn.db'}")
    # Only sessions exists — no messages/events, no alembic_version stamp.
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE sessions (id TEXT PRIMARY KEY)"))

    drop_stale_kernel_tables(engine)

    assert "sessions" not in _tables(engine)


def test_should_drop_precutover_fossils(tmp_path) -> None:
    """Pre-cutover ``projects`` / ``agents`` fossils alongside a foreign-stamped
    trio are cleared (they are kernel-owned table names)."""
    engine = create_engine(f"sqlite:///{tmp_path / 'fossil.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE projects (id TEXT PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE agents (id TEXT PRIMARY KEY)"))
        _create_kernel_trio(conn, stamp="0099")

    drop_stale_kernel_tables(engine)

    remaining = _tables(engine)
    assert not ({"projects", "agents"} | _TRIO) & remaining


def test_should_not_touch_host_or_checkpoint_tables(tmp_path) -> None:
    """The reset is kernel-scoped: host ``valuz_*`` tables, the host alembic
    stamp, and langgraph checkpoint tables survive even when the trio drops."""
    engine = create_engine(f"sqlite:///{tmp_path / 'mixed.db'}")
    with engine.begin() as conn:
        _create_kernel_trio(conn, stamp="0099")  # foreign → trio dropped
        conn.execute(text("CREATE TABLE valuz_agent (id TEXT PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE alembic_version_host (version_num TEXT PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE checkpoints (thread_id TEXT PRIMARY KEY)"))

    drop_stale_kernel_tables(engine)

    remaining = _tables(engine)
    assert not (_TRIO & remaining)
    assert {"valuz_agent", "alembic_version_host", "checkpoints"} <= remaining


def test_should_noop_on_fresh_install(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    drop_stale_kernel_tables(engine)
    assert _tables(engine) == set()
