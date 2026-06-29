"""Dependency injection — manages DB engine, session factory, store, and orchestrator lifecycle."""

from __future__ import annotations

import asyncio
import importlib
import logging
import os
from typing import Annotated

from app.config import AppConfig
from fastapi import Header, HTTPException
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from src.adapters.durable_outbox import DurableOutbox
from src.adapters.remote_store import build_remote_store
from src.adapters.sqlalchemy_store.engine import create_engine, create_session_factory
from src.adapters.sqlalchemy_store.store import SQLAlchemyStore
from src.adapters.write_through_store import WriteThroughStore
from src.core import NullTokenVerifier, StorePort, TokenVerifier
from src.core.orchestrator import SessionOrchestrator

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
# In-process durable Postgres engine (``kernel_store=pg``); disposed on shutdown
# alongside ``_engine``. ``None`` when local-only or the durable is HTTP-remote.
_durable_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None
_store: StorePort | None = None
_orchestrator: SessionOrchestrator | None = None
# Background drainer for the best-effort (``pg``) write-through outbox; cancelled
# on shutdown. ``None`` in strict (``remote``) / local-only modes.
_outbox_drainer: asyncio.Task[None] | None = None
# Owner-from-token seam: OSS default never derives identity from a token, so
# ``get_owner_id`` keeps using the trusted ``X-Valuz-Owner-Id`` header. A SaaS
# overlay binds a real verifier via ``set_token_verifier``.
_token_verifier: TokenVerifier = NullTokenVerifier()


async def init_dependencies(config: AppConfig) -> None:
    """Initialize DB engine, session factory, store, and orchestrator.

    Also runs the orphan-pending scan: any ``requires_action`` event left
    open across a host restart is sealed with
    ``action_resolved(decision="expired", resolved_by="system")``
    (per design doc §6.3 — D6 contract symmetry across runtimes).
    """
    global _engine, _session_factory, _store, _orchestrator  # noqa: PLW0603
    global _durable_engine, _outbox_drainer  # noqa: PLW0603
    # Model A: the LOCAL store ALWAYS exists (local-first). The kernel keeps its
    # own SQLite/PG via this engine; when a durable backend is configured
    # (remote DataService / central PG) every write is mirrored through it
    # (WriteThroughStore). No "remote replaces local" branch.
    _engine = create_engine(config.database_url)
    _session_factory = create_session_factory(_engine)
    local: StorePort = SQLAlchemyStore(_session_factory)
    durable = _build_durable_store(config)
    if _durable_engine is not None:
        # In-process durable (``kernel_store=pg``): create the kernel schema if
        # absent. ``create_all`` is checkfirst (idempotent) — a no-op when the
        # DB was already provisioned by alembic, and it materializes the full
        # current model (incl. the ``event_uid`` unique index) on a fresh PG.
        await _ensure_durable_schema(_durable_engine)
    store: StorePort = _wrap_durable(config, local, durable)
    _store = store
    _orchestrator = SessionOrchestrator(
        store,
        max_warm_runtimes=_env_int("VALUZ_MAX_WARM_RUNTIMES"),
        runtime_idle_ttl_s=_env_float("VALUZ_RUNTIME_IDLE_TTL_S"),
    )
    # Start the warm-runtime idle sweeper (bounds leaked claude/codex
    # subprocesses; see SessionOrchestrator). Safe before the orphan scan's
    # possible early return so it runs regardless of migration state.
    _orchestrator.start()
    # Best-effort (``pg``) write-through: re-push any backlog left by a prior run,
    # then keep a background drainer alive so a recovered durable catches up even
    # without new writes. Strict / local-only modes have no outbox.
    if isinstance(store, WriteThroughStore) and config.kernel_store == "pg":
        try:
            await store.drain_outbox()
        except Exception:  # noqa: BLE001 — durable may still be down at boot
            logger.debug("startup outbox drain failed", exc_info=True)
        _outbox_drainer = asyncio.create_task(_outbox_drain_loop(store))
    # Orphan scans run against the always-present local store.
    # Best-effort — schema may not be migrated yet (typical in unit tests that
    # skip Alembic and run against an empty in-memory DB).
    try:
        sealed = await _orchestrator.scan_orphan_pendings()
        reset_runs = await _orchestrator.scan_orphan_runs()
    except OperationalError as exc:
        logger.debug("Orphan scan skipped (schema not migrated): %s", exc)
        return
    if sealed:
        logger.info("Sealed %d orphan pending approval(s) on startup", sealed)
    if reset_runs:
        logger.info("Reset %d orphan running session(s) on startup", reset_runs)


async def shutdown_dependencies() -> None:
    """Dispose engine and clear singletons. Called during app lifespan shutdown."""
    global _engine, _durable_engine, _session_factory, _store, _orchestrator  # noqa: PLW0603
    global _outbox_drainer  # noqa: PLW0603
    if _outbox_drainer is not None:
        # Stop the best-effort outbox drainer; a final drain is skipped (the
        # backlog is durable in the local DB and re-pushed on next startup).
        _outbox_drainer.cancel()
        try:
            await _outbox_drainer
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001 — shutdown must not raise
            logger.debug("outbox drainer shutdown error", exc_info=True)
    if _orchestrator is not None:
        # Cancel the idle sweeper and close every warm runtime — terminates all
        # live claude/codex subprocesses deterministically on shutdown.
        try:
            await _orchestrator.shutdown()
        except Exception:  # noqa: BLE001 — shutdown must not raise
            logger.debug("orchestrator shutdown failed", exc_info=True)
    if _engine:
        await _engine.dispose()
    if _durable_engine:
        await _durable_engine.dispose()
    _engine = None
    _durable_engine = None
    _session_factory = None
    _store = None
    _orchestrator = None
    _outbox_drainer = None


def _env_int(name: str) -> int | None:
    """Parse an optional int env override (``<= 0`` disables the policy);
    ``None`` (use default) when unset or malformed."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    try:
        return int(raw)
    except ValueError:
        logger.warning("Ignoring malformed %s=%r (expected int)", name, raw)
        return None


def _env_float(name: str) -> float | None:
    """Parse an optional float env override; ``None`` (use default) when unset
    or malformed."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    try:
        return float(raw)
    except ValueError:
        logger.warning("Ignoring malformed %s=%r (expected number)", name, raw)
        return None


def get_owner_id(
    x_valuz_owner_id: Annotated[str | None, Header()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    """FastAPI dependency — the request's owner id (``user_id``).

    Two sources, in order:

    1. **Verified token** (remote / SaaS): when a ``TokenVerifier`` is bound and
       a bearer token is present, the owner comes from the VERIFIED token claims
       — never from a caller-supplied header (an untrusted sandbox could forge
       ``X-Valuz-Owner-Id``). OSS binds ``NullTokenVerifier`` (always ``None``),
       so this branch is inert and behaviour is unchanged.
    2. **Header** (trusted host mount): the host sends the resolved per-request
       owner in ``X-Valuz-Owner-Id``. The in-process seam never reaches this
       dependency — it passes the owner explicitly. An absent header on a direct
       HTTP call is a 403; the kernel never serves owner-scoped data without one.
    """
    claims = _token_verifier.verify(_bearer_token(authorization))
    if claims is not None:
        return claims.user_id
    if not x_valuz_owner_id:
        raise HTTPException(status_code=403, detail="owner id required")
    return x_valuz_owner_id


def _bearer_token(authorization: str | None) -> str | None:
    """Extract the bearer credential from an ``Authorization`` header."""
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        return value.strip()
    return None


def set_token_verifier(verifier: TokenVerifier) -> None:
    """Bind the owner-from-token verifier. A SaaS overlay swaps the default
    ``NullTokenVerifier`` for a signing-key/JWKS-backed implementation so the
    sandbox's owner is derived from its verified JWT, not a header."""
    global _token_verifier  # noqa: PLW0603
    _token_verifier = verifier


def _build_durable_store(config: AppConfig) -> StorePort | None:
    """The durable write-through target (model A), or ``None`` for local-only.

    ``kernel_store=pg`` → an in-process ``SQLAlchemyStore`` on
    ``durable_database_url`` (same process, no HTTP; the OSS "configure a
    Postgres" path). Its engine is stashed on ``_durable_engine`` so the
    lifespan shutdown disposes it.

    ``kernel_store=remote`` + ``data_api_url`` → a client to the remote
    DataService (sandbox/SaaS); the local store is mirrored to it. No engine /
    DSN here — only the data-API URL + a bearer-token hook (the concrete
    backend ``data_api_kind`` self-registers on import).

    Returns ``None`` (``kernel_store=local``) when no durable backend is
    configured — the kernel runs local-only.
    """
    global _durable_engine  # noqa: PLW0603
    if config.kernel_store == "pg":
        if not config.durable_database_url:
            raise RuntimeError("KERNEL_STORE=pg requires VALUZ_DURABLE_DATABASE_URL")
        _durable_engine = create_engine(config.durable_database_url)
        return SQLAlchemyStore(create_session_factory(_durable_engine))
    if config.kernel_store != "remote":
        return None
    if not config.data_api_url:
        raise RuntimeError("KERNEL_STORE=remote requires VALUZ_DATA_API_URL")
    _ensure_remote_backend(config.data_api_kind)
    token = config.data_api_token or ""

    async def _access_token() -> str:
        # Static token from env for now; a refresh hook (re-mint short-lived
        # JWT from the host) plugs in here later.
        return token

    return build_remote_store(
        kind=config.data_api_kind,
        base_url=config.data_api_url,
        access_token=_access_token,
    )


def _wrap_durable(config: AppConfig, local: StorePort, durable: StorePort | None) -> StorePort:
    """Compose the effective store from the (always-present) local + optional durable.

    - no durable → local-only (single write).
    - ``remote`` → STRICT write-through (durable-first events, fail-loud).
    - ``pg`` → BEST-EFFORT write-through: local is authoritative; durable failures
      land in a :class:`DurableOutbox` over the LOCAL session factory so a Postgres
      outage never blocks local-first writes.
    """
    if durable is None:
        return local
    if config.kernel_store == "pg":
        assert _session_factory is not None  # set just above in init
        return WriteThroughStore(
            local,
            durable,
            durable_required=False,
            outbox=DurableOutbox(_session_factory),
        )
    return WriteThroughStore(local, durable, durable_required=True)


async def _outbox_drain_loop(store: WriteThroughStore) -> None:
    """Periodically re-push queued durable writes (best-effort mode)."""
    interval = _env_float("VALUZ_OUTBOX_DRAIN_INTERVAL_S") or 30.0
    while True:
        await asyncio.sleep(interval)
        try:
            drained = await store.drain_outbox()
            if drained:
                logger.info("durable outbox: re-pushed %d op(s)", drained)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — keep the loop alive across durable blips
            logger.debug("outbox drain loop iteration failed", exc_info=True)


async def _ensure_durable_schema(engine: AsyncEngine) -> None:
    """Create the kernel schema on the in-process durable engine if missing.

    Idempotent (``create_all`` is checkfirst). Used only for ``kernel_store=pg``;
    the HTTP-remote durable owns its own (externally migrated) schema.
    """
    from src.adapters.sqlalchemy_store.models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def _ensure_remote_backend(kind: str) -> None:
    """Import the module that self-registers ``kind`` (Phase B: postgrest)."""
    module = {
        "http": "src.adapters.remote_store_http",
        "postgrest": "src.adapters.remote_store_postgrest",
    }.get(kind)
    if module:
        try:
            importlib.import_module(module)
        except ImportError:
            logger.debug("remote store backend module %s not importable yet", module)


def get_store() -> StorePort:
    """FastAPI dependency — returns the StorePort singleton."""
    if _store is None:
        raise RuntimeError("Dependencies not initialized — is the app lifespan running?")
    return _store


def get_orchestrator() -> SessionOrchestrator:
    """FastAPI dependency — returns the SessionOrchestrator singleton."""
    if _orchestrator is None:
        raise RuntimeError("Dependencies not initialized — is the app lifespan running?")
    return _orchestrator
