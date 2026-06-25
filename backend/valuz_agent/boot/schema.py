"""Host-side schema bootstrap — incremental alembic chain.

The host owns its own alembic chain at ``backend/alembic/host`` with a
non-default ``version_table = alembic_version_host`` so it does NOT
collide with the kernel's ``alembic_version`` row in the same SQLite
file.

The chain is incremental: the 0001 baseline creates the schema and later
revisions ALTER it. ``drop_stale_host_tables`` keeps any DB stamped at a
*known* revision and lets ``run_host_migrations`` (``alembic upgrade head``)
migrate it forward — data-preserving. Only an unknown/foreign/corrupt stamp
(or tables present with no stamp) is dropped wholesale and re-initialized.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy import Engine

logger = logging.getLogger(__name__)

# Module-relative paths so the bootstrap works regardless of CWD.
# schema.py is at backend/valuz_agent/boot/; parents[2] is backend/, and the
# host alembic chain now lives at backend/alembic/host (moved out of the package).
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_DIR = _BACKEND_ROOT / "alembic" / "host"
ALEMBIC_INI = ALEMBIC_DIR / "alembic.ini"
VERSION_TABLE = "alembic_version_host"

# Head revision of the host alembic chain (kept for reference / exports). The
# chain is incremental now: ``drop_stale_host_tables`` trusts any DB on a
# *known* revision and lets ``alembic upgrade head`` migrate it forward
# (data-preserving); only an unknown/foreign/corrupt stamp is dropped + rebuilt.
BASELINE_REVISION = "0004"


def _known_host_revisions() -> set[str]:
    """Every revision id in the host alembic chain.

    A DB stamped at any of these is on a valid upgrade path and is migrated
    forward by ``alembic upgrade head`` (data-preserving) — see
    ``drop_stale_host_tables``.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    return {rev.revision for rev in ScriptDirectory.from_config(cfg).walk_revisions()}


def _any_rows(engine: Engine, tables: list[str]) -> bool:
    """True if any of ``tables`` holds at least one row. On a read error, assume
    data IS present (conservative — never wipe what we can't inspect)."""
    from sqlalchemy import text

    with engine.connect() as conn:
        for table in tables:
            try:
                row = conn.execute(
                    text(f'SELECT 1 FROM "{table}" LIMIT 1')  # noqa: S608
                ).first()
            except Exception:
                return True
            if row is not None:
                return True
    return False


def drop_stale_host_tables(engine: Engine | None = None) -> None:
    """Self-heal probe for an unstamped / corrupt host schema (incremental chain).

    The host alembic chain is incremental. This keeps any DB stamped at a
    *known* revision and lets ``run_host_migrations`` (``alembic upgrade
    head``) migrate it forward — data-preserving.

    Behaviour on a non-known stamp:

    - **Unknown revision WITH ``valuz_*`` data** → the data dir was written by a
      NEWER or divergent build (a downgrade). We **refuse to start** (raise)
      rather than drop the data — it stays intact so the user can run a build
      that knows the revision. (This deliberately does NOT wipe; an older drop
      behaviour silently destroyed downgraded data.)
    - **No stamp at all, with tables present** (a boot that died mid-init), or an
      orphan version table with no business data → drop-and-rebuild so the
      upgrade re-initialises cleanly from baseline. Nothing recoverable is lost.

    No-op on a fresh file / known stamp. Runs synchronously off the event loop —
    it owns no session and reads no business data, like the kernel probe.
    """
    from sqlalchemy import create_engine, inspect, text

    from valuz_agent.infra.config import settings

    owns_engine = engine is None
    if engine is None:
        engine = create_engine(settings.db_url)
    try:
        inspector = inspect(engine)
        existing = set(inspector.get_table_names())

        stamp: str | None = None
        if VERSION_TABLE in existing:
            with engine.connect() as conn:
                row = conn.execute(
                    text(f"SELECT version_num FROM {VERSION_TABLE}")  # noqa: S608
                ).fetchone()
                stamp = row[0] if row else None

        if stamp in _known_host_revisions():
            return  # known revision — `alembic upgrade head` migrates it

        business = sorted(t for t in existing if t.startswith("valuz_"))

        if stamp is not None and _any_rows(engine, business):
            # Downgrade / divergent build: a real revision this build can't
            # migrate, with actual data on the line. Fail loud, preserve the data.
            raise RuntimeError(
                f"host schema stamp={stamp!r} is not a known revision for this "
                f"build, but {len(business)} host table(s) hold data. Refusing to "
                f"start so nothing is deleted — the data dir was written by a newer "
                f"or divergent version. Run a build whose migrations include "
                f"{stamp!r} (usually: update to the latest)."
            )

        # No recoverable data at risk (unstamped half-init, an orphan version
        # table, or empty tables): drop the stale stamp/tables so the upgrade
        # reinitialises clean.
        stale = list(business)
        if VERSION_TABLE in existing:
            stale.append(VERSION_TABLE)
        if not stale:
            return  # fresh install — nothing to reset

        logger.warning(
            "host schema stamp=%s with no recoverable data — dropping %d table(s) "
            "for a clean re-initialization",
            stamp,
            len(stale),
        )
        with engine.begin() as conn:
            for table in stale:
                conn.execute(text(f"DROP TABLE IF EXISTS {table}"))
    finally:
        if owns_engine:
            engine.dispose()


def run_host_migrations() -> None:
    """Run host ``alembic upgrade head`` against the async (aiosqlite) DB URL.

    The host alembic ``env.py`` is async (``asyncio.run``), so — like
    ``run_kernel_migrations`` — this runs in a dedicated thread: the app startup
    hook is already on the event loop, and a nested ``asyncio.run`` there would
    raise. ``DATABASE_URL`` is set to ``settings.db_url_async`` so ``env.py``'s
    ``get_url()`` picks up the same SQLite file the rest of the host talks to,
    then restored on exit.
    """
    import os
    import threading

    from valuz_agent.infra.config import settings

    db_url = settings.db_url_async

    def _do() -> None:
        from alembic.config import Config

        from alembic import command

        # Reset any DB not stamped at the current baseline before upgrading so
        # the schema rebuilds clean (runs here, off the event loop, in the
        # same dedicated thread as the upgrade).
        drop_stale_host_tables()

        cfg = Config(str(ALEMBIC_INI))
        cfg.set_main_option("script_location", str(ALEMBIC_DIR))
        cfg.set_main_option("sqlalchemy.url", db_url)
        previous = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = db_url
        try:
            command.upgrade(cfg, "head")
        finally:
            if previous is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = previous

    error: list[BaseException] = []

    def _runner() -> None:
        try:
            _do()
        except BaseException as exc:  # noqa: BLE001 — re-raised on the caller thread
            error.append(exc)

    thread = threading.Thread(target=_runner, name="host-alembic-upgrade", daemon=True)
    thread.start()
    thread.join()
    if error:
        raise error[0]


__all__ = ["run_host_migrations", "drop_stale_host_tables", "VERSION_TABLE", "BASELINE_REVISION"]
