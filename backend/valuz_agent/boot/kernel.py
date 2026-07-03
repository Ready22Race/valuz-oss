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

from valuz_agent.infra.db_urls import (
    db_url_async,
    is_sqlite_runtime,
    kernel_db_url,
    kernel_db_url_async,
    sqlite_path_from_url,
)

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
    os.environ["DATABASE_URL"] = kernel_db_url_async()
    kernel_db_path = sqlite_path_from_url(kernel_db_url())
    if kernel_db_path is not None:
        os.environ.setdefault("DEEPAGENTS_CHECKPOINT_DB", str(kernel_db_path))
    # OSS default (KERNEL_STORE local/unset): the DataService backend is the host
    # sqlite (valuz.db). Inject it as the durable so the kernel dual-writes
    # kernel.db -> valuz.db and reads are served from the DataService.
    if os.environ.get("KERNEL_STORE", "local") == "local":
        os.environ.setdefault("VALUZ_DURABLE_DATABASE_URL", db_url_async())


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
        engine = create_async_engine(kernel_db_url_async())
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
    cfg.set_main_option("sqlalchemy.url", kernel_db_url_async())

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



async def init_kernel_dependencies() -> None:
    """Initialize the kernel's engine/session/store/orchestrator singletons.

    Mirrors ``app.dependencies.init_dependencies``. The store tier
    (``KERNEL_STORE`` / ``VALUZ_DURABLE_DATABASE_URL`` / ``VALUZ_DATA_API_*``) is
    read straight from the environment by the kernel's ``AppConfig`` — OSS
    configures the data service purely via env vars, loaded at boot.
    """
    _set_kernel_env()
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
    if is_sqlite_runtime() and getattr(kernel_deps, "_engine", None) is not None:
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


def make_data_service_placeholder():
    """Create the host-mounted DataService sub-app. Store + verifier are bound
    later in the lifespan (once the backend DSN + secret are known); until then
    ``/health`` and ``/openapi.json`` work and ``/rpc`` returns 401. Mounted at
    ``/internal/data`` by the host app factory."""
    from app.data_service import create_data_service_app
    from src.core.token_verifier import NullTokenVerifier

    return create_data_service_app(store=None, verifier=NullTokenVerifier())


def build_host_data_service_store(backend_dsn: str):
    """Build a ``(StorePort, AsyncEngine)`` over the host DataService backend.

    The host owns the DB credential here; a sandbox reaches this DataService
    over HTTP+JWT and never sees the DSN. RLS GUC is installed (no-op on SQLite).
    """
    from app.data_service import install_rls_guc
    from src.adapters.sqlalchemy_store.engine import create_engine, create_session_factory
    from src.adapters.sqlalchemy_store.store import SQLAlchemyStore

    engine = create_engine(backend_dsn)
    install_rls_guc(engine)
    return SQLAlchemyStore(create_session_factory(engine)), engine


async def ensure_host_data_service_schema(engine) -> None:
    """Create the kernel DATA schema on the host DataService backend if absent
    (checkfirst; idempotent vs. an already-migrated PG).

    Excludes ``durable_outbox`` — it is the LOCAL store's compensation queue
    (pending durable writes), so it belongs only on the kernel's local engine
    (kernel.db), never on the durable itself.
    """
    from src.adapters.sqlalchemy_store.models import Base

    durable_tables = [t for n, t in Base.metadata.tables.items() if n != "durable_outbox"]
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=durable_tables))


def make_host_data_service_verifier(secret: str):
    """HS256 verifier for the host-mounted DataService (sandbox tokens).

    Single-secret: assumes one owner (OSS local). For a shared multi-tenant host
    use ``make_host_data_service_verifier_per_owner``.
    """
    from src.core.token_signer import HmacTokenVerifier

    return HmacTokenVerifier(secret)


class _PerOwnerDataServiceVerifier:
    """``TokenVerifier`` that resolves the signing secret **per token owner**.

    Each owner's data-service token is signed with that owner's per-owner secret
    (``data_service_secret``). On a shared multi-tenant host, verification must
    read the token's ``sub`` (unverified), look up **that** owner's secret, then
    verify. Security: the ``sub`` is only a hint to pick the key — a forged
    ``sub`` fails the signature check (attacker lacks the victim's secret); and an
    owner with **no** secret is rejected (read-only lookup, never minted here), so
    an unauthenticated request cannot pollute the store. Unifies local (one owner
    resolves its own secret) and cloud (many owners).
    """

    def verify(self, token: str | None):
        if not token:
            return None
        import base64
        import json

        from src.core.token_signer import HmacTokenVerifier, InvalidTokenError

        from valuz_agent.infra.data_service_secret import DS_SECRET_REF
        from valuz_agent.infra import secret_store

        parts = token.split(".")
        if len(parts) != 3:
            raise InvalidTokenError("malformed token")
        try:
            claims = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
        except Exception as exc:  # noqa: BLE001 — any decode failure = malformed
            raise InvalidTokenError("malformed claims") from exc
        sub = claims.get("sub")
        if not sub:
            raise InvalidTokenError("missing sub")
        secret = secret_store.get(str(sub), DS_SECRET_REF)  # read-only; None if absent
        if not secret:
            raise InvalidTokenError("unknown owner")
        # Real verification (signature, alg pin, exp) with the owner's secret.
        return HmacTokenVerifier(secret).verify(token)


def make_host_data_service_verifier_per_owner() -> _PerOwnerDataServiceVerifier:
    """Per-owner HS256 verifier for the host DataService (multi-tenant)."""
    return _PerOwnerDataServiceVerifier()


def mint_data_service_token(
    secret: str, *, user_id: str, session_id: str | None = None, ttl_s: int = 86400
) -> str:
    """Mint a short-lived HS256 token for a sandbox to call the host DataService.
    The sandbox carries only this token — never the DB credential."""
    from src.core.token_signer import TokenSigner

    return TokenSigner(secret).sign(user_id=user_id, session_id=session_id, ttl_s=ttl_s)


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
