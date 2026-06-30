"""Boot the Agent Harness V5 kernel inside the valuz host process.

The kernel ships under ``backend/kernel/`` with bare top-level imports
(``from src.core ...``, ``from app.config ...``). Importing the ``kernel``
package puts that directory on ``sys.path`` so those imports resolve.

This module is the only place that:
- runs the kernel's Alembic migrations against the valuz SQLite file,
- initializes the kernel's dependency singletons against the same file,
- exposes the kernel's FastAPI routers to the valuz app.

Anything else in valuz that needs the kernel goes through ``get_orchestrator``
or ``get_store`` here.

Note (kernel V5 post-MODEL_CATALOG): the kernel no longer maintains an
internal model catalog. Every kernel ``Session`` carries its own
``model_provider`` (base_url + api_key + api_protocol); the runtime
factory dispatches on ``api_protocol``. Valuz composes the provider at
session creation time from the user-selected channel + (optional) alias —
see ``valuz_agent.adapters.provider_resolver``.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from valuz_agent.infra.config import settings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

# Triggers sys.path injection so ``from src.core...`` and ``from app.config...``
# resolve once anyone in the host imports the kernel package.
import kernel  # noqa: F401, E402  (side-effect import)

KERNEL_DIR: Path = Path(__file__).resolve().parents[2] / "kernel"
# The kernel alembic chain was moved out of the kernel tree to
# backend/alembic/kernel (sibling of the host chain at backend/alembic/host).
KERNEL_ALEMBIC_DIR: Path = Path(__file__).resolve().parents[2] / "alembic" / "kernel"
KERNEL_ALEMBIC_INI: Path = KERNEL_ALEMBIC_DIR / "alembic.ini"

# The kernel chain stamps the default ``alembic_version`` table (the host chain
# uses ``alembic_version_host`` in the same file so the two never collide).
KERNEL_VERSION_TABLE = "alembic_version"
# Kernel-owned tables the schema preflight inspects (never drops) — the
# current trio plus pre-cutover fossils. Host ``valuz_*`` tables and the DeepAgents
# langgraph checkpoint tables in the same file are off-limits.
_KERNEL_OWNED_TABLES = ("sessions", "messages", "events", "projects", "agents", "environments")


def _set_kernel_env() -> None:
    """Make the kernel see the valuz database URL and a sane workspace dir.

    The kernel's ``app.config.AppConfig`` reads ``DATABASE_URL`` from
    os.environ at construction time, so we set it before anything imports
    ``app.config``.

    ``DEEPAGENTS_CHECKPOINT_DB`` points the kernel's DeepAgentsRuntime
    langgraph checkpointer at the kernel's OWN SQLite file (``kernel.db``),
    alongside ``sessions/messages/events`` — so the checkpoint tables
    (``checkpoints`` / ``writes`` / ``checkpoint_blobs``) travel with the
    kernel into a sandbox/remote deployment instead of being stranded in the
    host ``valuz.db``. No stray ``./deepagents_checkpoints.db`` in whatever
    cwd happened to be active at first boot; setdefault honours an external
    override.
    """
    os.environ["DATABASE_URL"] = settings.kernel_db_url_async
    os.environ.setdefault("DEEPAGENTS_CHECKPOINT_DB", str(settings.kernel_db_path))


def _known_kernel_revisions() -> set[str]:
    """Every revision id in the kernel alembic chain.

    A DB stamped at any of these is on a valid upgrade path and is migrated
    forward by ``alembic upgrade head`` (data-preserving) — see
    ``ensure_kernel_schema_migratable``.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(KERNEL_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(KERNEL_ALEMBIC_DIR))
    return {rev.revision for rev in ScriptDirectory.from_config(cfg).walk_revisions()}


async def _any_kernel_rows(engine: AsyncEngine, tables: list[str]) -> bool:
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


async def ensure_kernel_schema_migratable(engine: AsyncEngine | None = None) -> None:
    """Preflight the kernel DB before ``alembic upgrade head`` — NEVER drops anything.

    Mirrors the host's ``boot.schema.ensure_host_schema_migratable``: the kernel
    alembic chain is incremental. Returns when the DB is safe to migrate (stamped
    at a *known* revision, or no kernel tables yet — a fresh file). Otherwise
    RAISES, deleting nothing:

    - an unknown/foreign stamp WITH kernel data → the store was written by a
      newer or divergent build (a downgrade); preserve it, run a build that knows
      the revision.
    - kernel tables present but unstamped / a foreign stamp with empty tables → a
      half-initialised / foreign DB; asks the operator to remove the data dir and
      restart. No committed data to lose, and still nothing is auto-deleted.

    Scoped to kernel-owned tables (``_KERNEL_OWNED_TABLES``); host ``valuz_*``
    tables and the langgraph checkpoint tables in the same file are never read or
    touched. No drops, ever. Reflects through an ASYNC engine (so a Postgres
    ``database_url`` resolves to asyncpg rather than choking a sync engine on an
    async driver); the caller runs it off the event loop in a worker thread.
    """
    from sqlalchemy import inspect, text
    from sqlalchemy.ext.asyncio import create_async_engine

    owns_engine = engine is None
    if engine is None:
        engine = create_async_engine(settings.kernel_db_url_async)
    try:
        async with engine.connect() as conn:
            existing = set(await conn.run_sync(lambda c: inspect(c).get_table_names()))

            stamp: str | None = None
            if KERNEL_VERSION_TABLE in existing:
                result = await conn.execute(
                    text(f"SELECT version_num FROM {KERNEL_VERSION_TABLE}")  # noqa: S608
                )
                row = result.fetchone()
                stamp = row[0] if row else None

        if stamp in _known_kernel_revisions():
            return  # known revision — `alembic upgrade head` migrates it forward

        owned = [t for t in _KERNEL_OWNED_TABLES if t in existing]
        if not owned:
            return  # fresh install / no kernel tables — alembic initialises it

        if await _any_kernel_rows(engine, owned):
            raise RuntimeError(
                f"kernel schema stamp={stamp!r} is not a known revision for this "
                f"build, but {len(owned)} kernel table(s) hold data. Refusing to "
                f"start — nothing is deleted. The kernel store was written by a "
                f"newer or divergent build (or lost its migration stamp); run a "
                f"build whose migrations include {stamp!r} (usually: update to the latest)."
            )

        raise RuntimeError(
            f"kernel schema is in an unrecognized state (stamp={stamp!r}) — kernel "
            f"table(s) present but no recoverable data (a half-initialised or "
            f"foreign DB). Nothing was deleted; remove the data dir and restart to "
            f"reinitialise cleanly."
        )
    finally:
        if owns_engine:
            await engine.dispose()


def _do_alembic_upgrade() -> None:
    _set_kernel_env()

    from alembic.config import Config

    from alembic import command

    cfg = Config(str(KERNEL_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(KERNEL_ALEMBIC_DIR))
    # The kernel alembic ``env.py`` prefers the ``DATABASE_URL`` env (set by
    # ``_set_kernel_env`` to ``kernel_db_url_async``) over this option, so the
    # two must agree on the kernel file — point the config at the kernel URL,
    # not the host ``db_url_async``, so the migration can never land on
    # ``valuz.db`` if the env is ever cleared.
    cfg.set_main_option("sqlalchemy.url", settings.kernel_db_url_async)

    command.upgrade(cfg, "head")


def run_kernel_migrations() -> None:
    """Apply the kernel's Alembic migrations to the valuz SQLite file.

    Two steps under one entry point:

    1. ``ensure_kernel_schema_migratable`` — preflight that NEVER drops. Trusts
       any DB stamped at a known kernel revision (the upgrade migrates it
       forward); an unknown/foreign/unstamped kernel schema makes boot fail loud
       (data preserved), never wiped. No-op on a healthy / fresh DB.
    2. The kernel's own alembic ``upgrade head``. Writes its revision into the
       default ``alembic_version`` table; the host's chain uses a separate
       ``alembic_version_host`` row in the same file so the two don't collide.
       Schema changes ship as new, reversible revisions chained onto the head —
       existing ``sessions`` / ``messages`` / ``events`` data migrates in place.

    Both steps run in a dedicated thread: the preflight reflects through an
    async engine (``asyncio.run``) and the kernel's ``alembic/env.py`` also
    calls ``asyncio.run()`` to drive its async migrations — either nested in the
    already-running FastAPI/Starlette startup loop would raise. Running them off
    the loop in a worker thread keeps the kernel migration code unchanged and the
    host code obvious at the call site.
    """
    import asyncio
    import threading

    error: list[BaseException] = []

    def _runner() -> None:
        try:
            # Preflight (async reflection) then the kernel alembic upgrade, both
            # off the event loop in this worker thread (see the docstring).
            asyncio.run(ensure_kernel_schema_migratable())
            _do_alembic_upgrade()
        except BaseException as exc:  # noqa: BLE001 — re-raised on the main thread
            error.append(exc)

    thread = threading.Thread(target=_runner, name="kernel-alembic-upgrade", daemon=True)
    thread.start()
    thread.join()
    if error:
        raise error[0]


async def _apply_data_service_env() -> None:
    """Translate the persisted Settings → Data Service config into the env the
    kernel's ``AppConfig`` reads (``KERNEL_STORE`` / ``VALUZ_DURABLE_DATABASE_URL``
    / ``VALUZ_DATA_API_*``). This is how a GUI store-tier choice reaches the
    IN-PROCESS kernel — applied at boot, before ``AppConfig()`` is constructed.

    Best-effort: a fresh DB (no settings yet) or any read error leaves the kernel
    at its default local-only store. Read directly via ``SettingsDatastore`` with
    the resolved local user (there is no request auth context at boot).
    """
    import json

    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.infra.local_identity import resolve_local_user_id
    from valuz_agent.modules.settings.datastore import SettingsDatastore
    from valuz_agent.modules.settings.preferences import (
        FALLBACK_DATA_API_KIND,
        KERNEL_STORE_VALUES,
        KEY_DATA_API_KIND,
        KEY_DATA_API_TOKEN,
        KEY_DATA_API_URL,
        KEY_DURABLE_DATABASE_URL,
        KEY_KERNEL_STORE,
    )

    def _val(row: object) -> str:
        value_json = getattr(row, "value_json", None)
        if not value_json:
            return ""
        try:
            data = json.loads(value_json)
        except (TypeError, ValueError):
            return ""
        value = data.get("value") if isinstance(data, dict) else None
        return value if isinstance(value, str) else ""

    try:
        owner = resolve_local_user_id()
        async with async_unit_of_work(commit=False) as db:
            ds = SettingsDatastore(db)
            store = _val(await ds.get_setting(owner, KEY_KERNEL_STORE)) or "local"
            if store not in KERNEL_STORE_VALUES or store == "local":
                return  # default / unset → leave the kernel local-only
            durable = _val(await ds.get_setting(owner, KEY_DURABLE_DATABASE_URL))
            api_url = _val(await ds.get_setting(owner, KEY_DATA_API_URL))
            api_kind = (
                _val(await ds.get_setting(owner, KEY_DATA_API_KIND)) or FALLBACK_DATA_API_KIND
            )
            api_token = _val(await ds.get_setting(owner, KEY_DATA_API_TOKEN))
    except Exception:  # noqa: BLE001 — never block boot on a settings read
        logger.debug("data-service config not applied (no settings yet?)", exc_info=True)
        return

    os.environ["KERNEL_STORE"] = store
    if store == "pg" and durable:
        os.environ["VALUZ_DURABLE_DATABASE_URL"] = durable
    if store == "remote":
        if api_url:
            os.environ["VALUZ_DATA_API_URL"] = api_url
        os.environ["VALUZ_DATA_API_KIND"] = api_kind
        if api_token:
            os.environ["VALUZ_DATA_API_TOKEN"] = api_token
    logger.info("kernel data-service: applied store tier %r from settings", store)


async def init_kernel_dependencies() -> None:
    """Initialize the kernel's engine/session/store/orchestrator singletons.

    Mirrors ``app.dependencies.init_dependencies`` but drives it from valuz
    settings instead of the kernel's own AppConfig defaults.
    """
    _set_kernel_env()
    await _apply_data_service_env()
    import app.dependencies as kernel_deps
    from app.config import AppConfig
    from app.dependencies import init_dependencies

    await init_dependencies(AppConfig())

    # No kernel-side owner default to seed: every kernel write stamps ``user_id``
    # explicitly (host → kernel_client → route → store), so there is nothing to
    # fall back to. Reads/writes that reach the kernel always carry an owner.

    # The kernel's engine factory (kernel/src/adapters/sqlalchemy_store/engine.py)
    # sets journal_mode=WAL but NOT busy_timeout, so kernel connections run with
    # SQLite's default busy_timeout=0. The kernel is the highest-frequency writer
    # during a turn (every coalesced event delta), so with timeout 0 it raises
    # "database is locked" *instantly* the moment the host's sync engine holds the
    # write lock — no wait, no retry. The host engine was hardened to 15s
    # (infra/database) but this kernel half of the SAME file was not, which is the
    # real source of the dispatch/scheduler lock storms. Attach the missing PRAGMA
    # to the kernel engine here (at the host seam), then dispose the pool so live
    # connections reconnect with it. The tidier home is the kernel's engine
    # factory — fold busy_timeout in there when next touching it.
    if settings.is_sqlite and getattr(kernel_deps, "_engine", None) is not None:
        from sqlalchemy import event as _sa_event

        kernel_engine = kernel_deps._engine

        @_sa_event.listens_for(kernel_engine.sync_engine, "connect")
        def _kernel_busy_timeout(dbapi_conn, _connection_record):  # type: ignore[no-untyped-def]
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA busy_timeout=15000")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()

        await kernel_engine.dispose()


async def shutdown_kernel_dependencies() -> None:
    from app.dependencies import shutdown_dependencies

    await shutdown_dependencies()


def get_kernel_routers() -> list:
    """Return the kernel's FastAPI routers in the order they should be mounted.

    Note: ``GET /api/v1/models`` was removed from the kernel along with the
    MODEL_CATALOG drop — runtime dispatch is now per-session protocol-driven,
    so there's no curated list to expose. Valuz surfaces models through its
    own ``/v1/channels`` API instead.

    Kernel V5+messages adds a ``messages`` router exposing
    ``GET /api/v1/sessions/{id}/messages`` /
    ``GET /api/v1/messages/{id}`` /
    ``GET /api/v1/messages/{id}/events`` so the frontend can read per-turn
    history (one row per ``run_turn``, with usage + todo snapshots).

    Per ADR-008 the kernel's ``app.routes.agents`` is *not* mounted here.
    Valuz keeps a private synthetic agent per project
    (``agent-<project_id>``); exposing the kernel CRUD surface would
    leak those rows to any frontend listing them, and we have no
    user-facing agent gallery yet. If/when product introduces agent
    presets, this decision is revisited in a new ADR.
    """
    from app.routes.events import router as events_router
    from app.routes.messages import router as messages_router
    from app.routes.run import router as run_router
    from app.routes.sessions import router as sessions_router
    from app.routes.usage import router as usage_router

    return [sessions_router, messages_router, run_router, events_router, usage_router]


def get_data_service_openapi() -> dict:
    """The DataService (``/rpc/{op}``) OpenAPI schema, for the settings panel.

    Built from the kernel's data-service app (no store / DB needed — the schema
    is derived from the route signatures). This is the contract the sandbox /
    SaaS client speaks; surfacing it lets the user inspect the data API. Lives
    in ``boot`` because that's the seam allowed to import ``app.*``.
    """
    from app.data_service import create_data_service_app
    from src.core.token_verifier import NullTokenVerifier

    return create_data_service_app(store=None, verifier=NullTokenVerifier()).openapi()
