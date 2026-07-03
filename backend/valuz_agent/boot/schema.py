"""Host-side schema bootstrap — incremental alembic chain.

The host owns its own alembic chain at ``backend/alembic/host`` with a
non-default ``version_table = alembic_version_host`` so it does NOT
collide with the kernel's ``alembic_version`` row in the same SQLite
file.

The chain is incremental: the 0001 baseline creates the schema and later
revisions ALTER it. ``ensure_host_schema_migratable`` keeps any DB stamped at a
*known* revision and lets ``run_host_migrations`` (``alembic upgrade head``)
migrate it forward — data-preserving. An unknown/foreign/corrupt stamp is
NEVER dropped: boot refuses to start (fail loud) so a downgraded store is kept.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

# Module-relative paths so the bootstrap works regardless of CWD.
# schema.py is at backend/valuz_agent/boot/; parents[2] is backend/, and the
# host alembic chain now lives at backend/alembic/host (moved out of the package).
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_DIR = _BACKEND_ROOT / "alembic" / "host"
ALEMBIC_INI = ALEMBIC_DIR / "alembic.ini"
VERSION_TABLE = "alembic_version_host"

# Head revision of the host alembic chain (kept for reference / exports). The
# chain is incremental now: ``ensure_host_schema_migratable`` trusts any DB on a
# *known* revision and lets ``alembic upgrade head`` migrate it forward
# (data-preserving); an unknown/foreign/corrupt stamp makes boot fail loud (never dropped).
BASELINE_REVISION = "0004"


def _known_host_revisions() -> set[str]:
    """Every revision id in the host alembic chain.

    A DB stamped at any of these is on a valid upgrade path and is migrated
    forward by ``alembic upgrade head`` (data-preserving) — see
    ``ensure_host_schema_migratable``.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    return {rev.revision for rev in ScriptDirectory.from_config(cfg).walk_revisions()}


async def _any_rows(engine: AsyncEngine, tables: list[str]) -> bool:
    """True if any of ``tables`` holds at least one row. On a read error, assume
    data IS present (conservative — never wipe what we can't inspect)."""
    from sqlalchemy import text

    async with engine.connect() as conn:
        for table in tables:
            try:
                result = await conn.execute(
                    text(f'SELECT 1 FROM "{table}" LIMIT 1')  # noqa: S608
                )
                row = result.first()
            except Exception:
                return True
            if row is not None:
                return True
    return False


async def ensure_host_schema_migratable(engine: AsyncEngine | None = None) -> None:
    """Preflight the host DB before ``alembic upgrade head`` — NEVER drops anything.

    The host alembic chain is incremental. Returns when the DB is safe to migrate:

    - stamped at a *known* revision → ``alembic upgrade head`` migrates it forward
      (data-preserving), or
    - a fresh DB (no host tables and no version table) → alembic initialises it.

    Otherwise RAISES (refusing to start, deleting nothing):

    - a stamp this build's chain doesn't contain, WITH ``valuz_*`` data → the data
      dir was written by a NEWER or divergent build (a downgrade), or lost its
      migration stamp. The data stays intact; run a build that knows the revision.
    - any other unrecognised / half-initialised state (tables present but
      unstamped, or a foreign stamp with empty tables) → asks the operator to
      remove the data dir and restart. There is no committed data to lose, and we
      still refuse to delete anything automatically.

    No drops, ever — an earlier revision wiped tables here and could destroy a
    downgraded store. Reflects through an ASYNC engine (so a Postgres
    ``database_url`` resolves to asyncpg rather than choking a sync engine on an
    async driver); the caller runs it off the event loop in a worker thread.
    Reads no business rows beyond a one-row existence probe.
    """
    from sqlalchemy import inspect, text
    from sqlalchemy.ext.asyncio import create_async_engine

    from valuz_agent.infra.db_urls import db_url_async

    owns_engine = engine is None
    if engine is None:
        engine = create_async_engine(db_url_async())
    try:
        async with engine.connect() as conn:
            existing = set(await conn.run_sync(lambda c: inspect(c).get_table_names()))

            stamp: str | None = None
            if VERSION_TABLE in existing:
                result = await conn.execute(
                    text(f"SELECT version_num FROM {VERSION_TABLE}")  # noqa: S608
                )
                row = result.fetchone()
                stamp = row[0] if row else None

        if stamp in _known_host_revisions():
            return  # known revision — `alembic upgrade head` migrates it forward

        business = sorted(t for t in existing if t.startswith("valuz_"))
        if not business and VERSION_TABLE not in existing:
            return  # fresh install — alembic initialises from baseline

        if await _any_rows(engine, business):
            raise RuntimeError(
                f"host schema stamp={stamp!r} is not a known revision for this "
                f"build, but {len(business)} host table(s) hold data. Refusing to "
                f"start — nothing is deleted. The data dir was written by a newer "
                f"or divergent build (or lost its migration stamp); run a build "
                f"whose migrations include {stamp!r} (usually: update to the latest)."
            )

        raise RuntimeError(
            f"host schema is in an unrecognized state (stamp={stamp!r}) — host "
            f"table(s) present but no recoverable data (a half-initialised or "
            f"foreign DB). Nothing was deleted; remove the data dir and restart to "
            f"reinitialise cleanly."
        )
    finally:
        if owns_engine:
            await engine.dispose()


def run_host_migrations() -> None:
    """Run host ``alembic upgrade head`` against the async (aiosqlite) DB URL.

    The host alembic ``env.py`` is async (``asyncio.run``), so — like
    ``run_kernel_migrations`` — this runs in a dedicated thread: the app startup
    hook is already on the event loop, and a nested ``asyncio.run`` there would
    raise. ``DATABASE_URL`` is set to ``db_url_async()`` so ``env.py``'s
    ``get_url()`` picks up the same SQLite file the rest of the host talks to,
    then restored on exit.
    """
    import os
    import threading

    from valuz_agent.infra.db_urls import db_url_async

    db_url = db_url_async()

    def _do() -> None:
        import asyncio

        from alembic.config import Config

        from alembic import command

        # Preflight: refuse (never drop) to upgrade a downgraded / unrecognised
        # DB before alembic runs. Reflects through an async engine; ``asyncio.run``
        # is safe here — this is the dedicated thread, off the startup event loop.
        asyncio.run(ensure_host_schema_migratable())

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


__all__ = [
    "run_host_migrations",
    "ensure_host_schema_migratable",
    "VERSION_TABLE",
    "BASELINE_REVISION",
]
