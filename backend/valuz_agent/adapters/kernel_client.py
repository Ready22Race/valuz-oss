"""KernelClient — the host's single operational seam to the kernel.

The method surface mirrors the kernel HTTP API one-to-one (see the table in
the module body); every input/output is a kernel **wire schema**
(``app.schemas`` Pydantic models), never a kernel domain dataclass. The
default ``InProcessKernelClient`` invokes the kernel's own route functions
directly with explicit dependencies — the exact code path HTTP requests
take, minus the network — so a future ``HttpKernelClient`` (remote kernel in
a cloud sandbox) can swap in behind the same protocol without touching call
sites.

Errors surface as ``Kernel*Error`` types owned by this module; the
in-process implementation maps the routes' ``HTTPException``s onto them
(an HTTP implementation would map status codes identically).

Endpoints below are shown under ``{KERNEL_API_PREFIX}`` — this host overrides
it to ``/kernel`` (ADR-013; the kernel's own upstream default is ``/api`` — see
``valuz_agent.boot.kernel.kernel_api_prefix`` /
``kernel/app/routes/__init__.py``).

| method                   | kernel endpoint                                            |
|--------------------------|-------------------------------------------------------------|
| create_session           | POST   {KERNEL_API_PREFIX}/v1/sessions                      |
| get_session              | GET    {KERNEL_API_PREFIX}/v1/sessions/{id}                 |
| list_sessions            | GET    {KERNEL_API_PREFIX}/v1/sessions[?status=&ids=]       |
| update_session           | PATCH  {KERNEL_API_PREFIX}/v1/sessions/{id}                 |
| delete_session           | DELETE {KERNEL_API_PREFIX}/v1/sessions/{id}                 |
| set_mode                 | POST   {KERNEL_API_PREFIX}/v1/sessions/{id}/mode             |
| finalize_session         | POST   {KERNEL_API_PREFIX}/v1/sessions/{id}/finalize          |
| append_event             | POST   {KERNEL_API_PREFIX}/v1/sessions/{id}/events            |
| emit_live_event          | POST   {KERNEL_API_PREFIX}/v1/sessions/{id}/events?live_only=true|
| get_events               | GET    {KERNEL_API_PREFIX}/v1/sessions/{id}/events[?after_seq=]|
| get_events_window        | GET    {KERNEL_API_PREFIX}/v1/sessions/{id}/events/window     |
| subscribe_session_events | SSE    {KERNEL_API_PREFIX}/v1/sessions/{id}/events/stream     |
| subscribe_all_events     | SSE    {KERNEL_API_PREFIX}/v1/events/stream                   |
| usage_rollup             | GET    {KERNEL_API_PREFIX}/v1/usage                            |
| list_messages            | GET    {KERNEL_API_PREFIX}/v1/sessions/{id}/messages           |
| submit_action            | POST   {KERNEL_API_PREFIX}/v1/sessions/{id}/actions            |
| interrupt                | POST   {KERNEL_API_PREFIX}/v1/sessions/{id}/interrupt          |
| run_turn                 | WS     {KERNEL_API_PREFIX}/v1/sessions/{id}/run                |
| scan_orphan_*            | (in-process only — no remote analog; the                     |
|                          |  kernel runs these itself at startup)                        |
"""

from __future__ import annotations

# mypy: disable-error-code="no-any-return"
# The kernel boundary is configured ``follow_imports = "skip"`` so kernel
# types resolve to ``Any``; silenced at module scope like the former
# kernel_store facade.

# ruff: noqa: I001 — the kernel side-effect import must precede ``app.*``.

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, NoReturn, Protocol, TypedDict

import valuz_agent.boot.kernel  # noqa: F401  (sys.path side-effect)

from fastapi import HTTPException  # noqa: E402

from valuz_agent.ports.sandbox_allocator import SandboxScope  # noqa: E402

logger = logging.getLogger(__name__)

from app.schemas import (  # noqa: E402
    CreateSessionRequest,
    EventData,
    EventPayload,
    EventWindowData,
    FinalizeSessionRequest,
    MessageData,
    SessionData,
    SetSessionModeRequest,
    SubmitActionRequest,
    UpdateSessionRequest,
    UsageRollupData,
)


# ---------------------------------------------------------------------------
# Errors — owned by the seam, independent of transport.
# ---------------------------------------------------------------------------


class KernelClientError(Exception):
    """Base for kernel seam failures. ``status`` follows HTTP semantics."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


class KernelSessionNotFoundError(KernelClientError):
    pass


class KernelBadRequestError(KernelClientError):
    pass


class KernelConflictError(KernelClientError):
    pass


class KernelGoneError(KernelClientError):
    pass


class KernelUnavailableError(KernelClientError):
    pass


class KernelNotImplementedError(KernelClientError):
    pass


def _raise_mapped(exc: HTTPException) -> NoReturn:
    detail = str(exc.detail)
    if exc.status_code == 404:
        raise KernelSessionNotFoundError(404, detail) from exc
    if exc.status_code == 400:
        raise KernelBadRequestError(400, detail) from exc
    if exc.status_code == 409:
        raise KernelConflictError(409, detail) from exc
    if exc.status_code == 410:
        raise KernelGoneError(410, detail) from exc
    if exc.status_code == 503:
        raise KernelUnavailableError(503, detail) from exc
    if exc.status_code == 501:
        raise KernelNotImplementedError(501, detail) from exc
    raise KernelClientError(exc.status_code, detail) from exc


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class RuntimeAvailability(TypedDict):
    """Per-runtime launchability, as reported by the kernel (§3.3)."""

    available: bool
    unavailable_reason: str | None


class KernelClient(Protocol):
    # Owner model (mirrors the host valuz_* tables): every owner-scoped method
    # takes the caller's ``user_id`` FIRST and the kernel filters/stamps on it.
    # ``list_all_sessions`` / ``subscribe_all_events`` / ``scan_orphan_*`` are
    # the deliberate cross-owner exceptions (startup sweeps + host aggregators).

    async def create_session(self, user_id: str, req: CreateSessionRequest) -> SessionData: ...

    async def get_session(self, user_id: str, session_id: str) -> SessionData | None: ...

    async def list_sessions(
        self,
        user_id: str,
        *,
        status: str | None = None,
        ids: list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SessionData]: ...

    async def list_all_sessions(
        self,
        *,
        status: str | None = None,
        ids: list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SessionData]: ...

    async def update_session(
        self, user_id: str, session_id: str, req: UpdateSessionRequest
    ) -> SessionData: ...

    async def delete_session(self, user_id: str, session_id: str) -> bool: ...

    async def set_mode(self, user_id: str, session_id: str, mode: str) -> SessionData: ...

    async def finalize_session(
        self, user_id: str, session_id: str, req: FinalizeSessionRequest
    ) -> SessionData: ...

    async def append_event(self, user_id: str, session_id: str, event: EventPayload) -> bool: ...

    async def emit_live_event(
        self, user_id: str, session_id: str, type: str, data: dict[str, Any]
    ) -> None: ...

    async def get_events(
        self,
        user_id: str,
        session_id: str,
        *,
        limit: int = 200,
        offset: int = 0,
        after_seq: int | None = None,
    ) -> list[EventData]: ...

    async def get_events_window(
        self, user_id: str, session_id: str, *, before_seq: int | None = None, turn_limit: int = 20
    ) -> EventWindowData: ...

    def subscribe_session_events(
        self, user_id: str, session_id: str
    ) -> AsyncIterator[EventData]: ...

    def subscribe_all_events(
        self, types: tuple[str, ...] | None = None
    ) -> AsyncIterator[EventData]: ...

    async def usage_rollup(
        self, user_id: str, start_ms: int, end_ms: int
    ) -> list[UsageRollupData]: ...

    async def list_messages(
        self, user_id: str, session_id: str, *, limit: int = 50, offset: int = 0
    ) -> list[MessageData]: ...

    async def submit_action(
        self, user_id: str, session_id: str, req: SubmitActionRequest
    ) -> dict[str, Any]: ...

    async def interrupt(self, user_id: str, session_id: str) -> None: ...

    async def run_turn(
        self,
        user_id: str,
        session_id: str,
        text: str,
        attachments: list[dict[str, Any]] | None = None,
        additional_context: str = "",
    ) -> MessageData: ...

    async def runtime_availability(self) -> dict[str, RuntimeAvailability]: ...

    async def bg_busy_session_ids(self) -> list[str]: ...


# ---------------------------------------------------------------------------
# In-process implementation — calls the kernel's route functions directly.
# ---------------------------------------------------------------------------


def _store() -> Any:
    from app.dependencies import get_store

    try:
        return get_store()
    except RuntimeError as exc:
        # The kernel's StorePort singleton is torn down on app-lifespan exit
        # (``shutdown_dependencies`` resets it to ``None``). An in-flight
        # in-process call landing here means the kernel is shutting down and no
        # longer serving. Surface it as the typed "unavailable" signal so
        # best-effort callers (e.g. the actor-loop finalize that races shutdown)
        # can skip quietly instead of crashing on a bare ``RuntimeError``.
        raise KernelUnavailableError(503, str(exc)) from exc


def _orchestrator() -> Any:
    from app.dependencies import get_orchestrator

    try:
        return get_orchestrator()
    except RuntimeError as exc:
        raise KernelUnavailableError(503, str(exc)) from exc


class InProcessKernelClient:
    """Default transport: the kernel lives in this process.

    Each method drives the same route function the HTTP surface mounts, so
    validation/serialization behaviour is identical by construction.
    """

    async def create_session(self, user_id: str, req: CreateSessionRequest) -> SessionData:
        from app.routes.sessions import create_session

        try:
            result = await create_session(req, _store(), user_id)
        except HTTPException as exc:
            _raise_mapped(exc)
        return result["data"]

    async def get_session(self, user_id: str, session_id: str) -> SessionData | None:
        from app.routes.sessions import get_session

        try:
            result = await get_session(session_id, _store(), user_id)
        except HTTPException as exc:
            if exc.status_code == 404:
                return None
            _raise_mapped(exc)
        return result["data"]

    async def list_sessions(
        self,
        user_id: str,
        *,
        status: str | None = None,
        ids: list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SessionData]:
        from app.routes.sessions import list_sessions

        try:
            result = await list_sessions(
                _store(),
                user_id,
                status=status,
                ids=",".join(ids) if ids is not None else None,
                limit=limit,
                offset=offset,
            )
        except HTTPException as exc:
            _raise_mapped(exc)
        return result["data"]

    async def list_all_sessions(
        self,
        *,
        status: str | None = None,
        ids: list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SessionData]:
        # Cross-owner sweep (startup recovery / host aggregators). Goes straight
        # to the store with ``user_id=None`` — the owner-injecting route can't
        # express "every owner". Serializes with the route's projection.
        from app.serializers import session_to_data

        sessions = await _store().list_sessions(
            None, status=status, ids=ids, limit=limit, offset=offset
        )
        return [session_to_data(s) for s in sessions]

    async def update_session(
        self, user_id: str, session_id: str, req: UpdateSessionRequest
    ) -> SessionData:
        from app.routes.sessions import update_session

        try:
            result = await update_session(session_id, req, _store(), user_id)
        except HTTPException as exc:
            _raise_mapped(exc)
        return result["data"]

    async def delete_session(self, user_id: str, session_id: str) -> bool:
        from app.routes.sessions import delete_session

        try:
            await delete_session(session_id, _store(), user_id, _orchestrator())
        except HTTPException as exc:
            if exc.status_code == 404:
                return False
            _raise_mapped(exc)
        return True

    async def set_mode(self, user_id: str, session_id: str, mode: str) -> SessionData:
        from app.routes.sessions import set_session_mode

        try:
            result = await set_session_mode(
                session_id, SetSessionModeRequest(mode=mode), _store(), _orchestrator(), user_id
            )
        except HTTPException as exc:
            _raise_mapped(exc)
        return result["data"]

    async def finalize_session(
        self, user_id: str, session_id: str, req: FinalizeSessionRequest
    ) -> SessionData:
        from app.routes.sessions import finalize_session

        try:
            result = await finalize_session(session_id, req, _store(), user_id)
        except HTTPException as exc:
            _raise_mapped(exc)
        return result["data"]

    async def append_event(self, user_id: str, session_id: str, event: EventPayload) -> bool:
        from app.routes.sessions import append_session_event

        try:
            result = await append_session_event(
                session_id, event, _store(), _orchestrator(), user_id, live_only=False
            )
        except HTTPException as exc:
            _raise_mapped(exc)
        return bool(result["data"].persisted)

    async def emit_live_event(
        self, user_id: str, session_id: str, type: str, data: dict[str, Any]
    ) -> None:
        from app.routes.sessions import append_session_event

        try:
            await append_session_event(
                session_id,
                EventPayload(type=type, data=data),
                _store(),
                _orchestrator(),
                user_id,
                live_only=True,
            )
        except HTTPException as exc:
            _raise_mapped(exc)

    async def get_events(
        self,
        user_id: str,
        session_id: str,
        *,
        limit: int = 200,
        offset: int = 0,
        after_seq: int | None = None,
    ) -> list[EventData]:
        from app.routes.sessions import get_session_events

        try:
            result = await get_session_events(
                session_id, _store(), user_id, limit=limit, offset=offset, after_seq=after_seq
            )
        except HTTPException as exc:
            _raise_mapped(exc)
        return result["data"]

    async def get_events_window(
        self, user_id: str, session_id: str, *, before_seq: int | None = None, turn_limit: int = 20
    ) -> EventWindowData:
        from app.routes.sessions import get_session_events_window

        try:
            result = await get_session_events_window(
                session_id, _store(), user_id, before_seq=before_seq, turn_limit=turn_limit
            )
        except HTTPException as exc:
            _raise_mapped(exc)
        return result["data"]

    async def subscribe_session_events(
        self, user_id: str, session_id: str
    ) -> AsyncIterator[EventData]:
        """Live tap on one session's event stream (no replay, no backfill —
        pair with ``get_events(after_seq=...)`` for catch-up reads).

        Remote analog: SSE {KERNEL_API_PREFIX}/v1/sessions/{id}/events/stream (ADR-013)."""
        from app.event_stream import QueueEventSink
        from app.serializers import live_event_to_data

        sink = QueueEventSink()
        orch = _orchestrator()
        await orch.attach_session_tap(user_id, session_id, sink)
        try:
            while True:
                event = await sink.queue.get()
                yield live_event_to_data(event)
        finally:
            await orch.detach_session_tap(session_id, sink)

    async def subscribe_all_events(
        self, types: tuple[str, ...] | None = None
    ) -> AsyncIterator[EventData]:
        """Live tap on EVERY session's event stream; frames carry
        ``session_id``. ``types`` is an event-type allowlist — a
        lifecycle-only consumer (the host control plane) filters here so
        token deltas are dropped at the source instead of shipped to be
        discarded. Remote analog: SSE {KERNEL_API_PREFIX}/v1/events/stream
        ?types=... (ADR-013)."""
        from app.event_stream import GlobalQueueTap
        from app.serializers import live_event_to_data

        tap = GlobalQueueTap()
        orch = _orchestrator()
        orch.attach_global_tap(tap)
        try:
            while True:
                session_id, event = await tap.queue.get()
                if types is not None and str(event.type) not in types:
                    continue
                yield live_event_to_data(event, session_id=session_id)
        finally:
            orch.detach_global_tap(tap)

    async def usage_rollup(self, user_id: str, start_ms: int, end_ms: int) -> list[UsageRollupData]:
        from app.routes.usage import get_usage_rollup

        try:
            result = await get_usage_rollup(_store(), user_id, start_ms=start_ms, end_ms=end_ms)
        except HTTPException as exc:
            _raise_mapped(exc)
        return result["data"]

    async def list_messages(
        self, user_id: str, session_id: str, *, limit: int = 50, offset: int = 0
    ) -> list[MessageData]:
        from app.routes.messages import list_session_messages

        try:
            result = await list_session_messages(
                session_id, _store(), user_id, limit=limit, offset=offset
            )
        except HTTPException as exc:
            _raise_mapped(exc)
        return result["data"]

    async def submit_action(
        self, user_id: str, session_id: str, req: SubmitActionRequest
    ) -> dict[str, Any]:
        from app.routes.sessions import submit_session_action

        try:
            result = await submit_session_action(session_id, req, _orchestrator(), user_id)
        except HTTPException as exc:
            _raise_mapped(exc)
        data = result["data"]
        return data if isinstance(data, dict) else data.model_dump()

    async def interrupt(self, user_id: str, session_id: str) -> None:
        # Remote analog: POST {KERNEL_API_PREFIX}/v1/sessions/{id}/interrupt
        # (ADR-013). Route the call through the owner-scoped interrupt route
        # so a cross-owner session_id 404s instead of interrupting another
        # owner's run.
        from app.routes.run import interrupt_session

        try:
            await interrupt_session(session_id, _store(), user_id)
        except HTTPException as exc:
            if exc.status_code == 404:
                return
            _raise_mapped(exc)

    async def run_turn(
        self,
        user_id: str,
        session_id: str,
        text: str,
        attachments: list[dict[str, Any]] | None = None,
        additional_context: str = "",
    ) -> MessageData:
        # Remote analog: the WS /run channel. The wire shape is
        # {"message": {"text": ..., "attachments": [...],
        #              "additional_context": ...}}; the returned MessageData
        # mirrors the channel's final message frame.
        from app.routes.messages import _message_to_data
        from src.core.types import Attachment, UserMessage

        atts = tuple(
            Attachment(
                source_path=a["source_path"],
                parsed_path=a.get("parsed_path"),
            )
            for a in (attachments or [])
        )
        message = await _orchestrator().run_turn(
            user_id,
            session_id,
            UserMessage(text=text, attachments=atts, additional_context=additional_context),
        )
        return _message_to_data(message)

    # -- In-process-only supervision hooks (no remote analog: a standalone
    # kernel runs its own orphan scans at startup; see app.dependencies). --

    async def scan_orphan_pendings(self) -> int:
        return await _orchestrator().scan_orphan_pendings()

    async def scan_orphan_runs(self) -> int:
        return await _orchestrator().scan_orphan_runs()

    async def cleanup_runtime(self, session_id: str) -> None:
        """Evict the cached runtime for ``session_id`` (in-process only —
        a remote kernel owns its runtime cache)."""
        await _orchestrator().cleanup(session_id)

    async def runtime_availability(self) -> dict[str, RuntimeAvailability]:
        # No store/orchestrator needed — a pure binary probe in this process
        # (host == kernel in-process, so this is the local-host answer).
        from app.routes.runtimes import get_runtime_availability

        result = await get_runtime_availability()
        return result["data"]

    async def bg_busy_session_ids(self) -> list[str]:
        """Sessions whose warm runtime carries a live background task.
        Process-scoped, id-only — callers intersect with their own
        owner-scoped session set (see the kernel route's docstring)."""
        return _orchestrator().bg_busy_session_ids()


def _make_client() -> KernelClient:
    """Bind the transport for this process from settings.

    ``inprocess`` (default) — the kernel lives in this process.
    ``http`` — the kernel runs as a separate process (bare subprocess,
    sandbox, or remote) at ``settings.kernel_url``; see
    ``adapters/kernel_client_http.py``.
    """
    from valuz_agent.infra.config import settings

    if settings.kernel_mode == "http":
        from valuz_agent.adapters.kernel_client_http import HttpKernelClient

        return HttpKernelClient(settings.kernel_url, token=settings.kernel_token)
    return InProcessKernelClient()


client: KernelClient = _make_client()


def rebind_client() -> None:
    """Re-select the transport from the current ``settings``.

    The module-level ``client`` is chosen once at import. When the kernel
    endpoint is decided at runtime (e.g. a sandbox provisioned at boot that
    sets ``kernel_mode=http`` + url/token), call this to swap the live
    object — the facade functions read the module global per call, so they
    pick up the new transport without re-import."""
    global client  # noqa: PLW0603
    client = _make_client()


# Module-level facade — call-site ergonomics match the former kernel_store
# (``await kernel_client.get_session(...)``), while the swappable object
# lives behind ``client`` for the HTTP transport.


# ---------------------------------------------------------------------------
# Per-user kernel resolution (fleet seam). EXECUTION / LIVE facade methods route
# through ``_kernel_for(user_id)``; STORE reads/writes stay on the durable path.
# The OSS default allocator returns endpoint=None → the process-global ``client``
# (in-process or boot-attached sandbox) — behavior unchanged. A commercial
# allocator returns a per-user endpoint; we cache one HttpKernelClient per URL.
# ---------------------------------------------------------------------------

_endpoint_clients: dict[str, KernelClient] = {}

# ---------------------------------------------------------------------------
# Sandbox scope resolution (per-session / per-task on-demand sandboxes).
#
# A scope names the unit of work a sandbox serves (see ``SandboxScope``). The
# facade derives it once per session and caches it — the mapping is immutable
# (a session never changes task membership). Callers that KNOW the scope at
# creation time (tasks pass ``task:{task_id}``) supply it explicitly; every
# other EXEC op resolves via the optional bound resolver (the tasks module
# binds a ``valuz_task_session`` lookup at boot) and falls back to
# ``session:{session_id}``. With the OSS ``BootSingletonAllocator`` the scope
# is ignored entirely — zero behavior change for local / single-user hosts.
# ---------------------------------------------------------------------------

_SCOPE_CACHE_MAX = 4096
_scope_cache: dict[str, SandboxScope] = {}
_scope_resolver: Callable[[str, str], Awaitable[SandboxScope | None]] | None = None


def bind_sandbox_scope_resolver(
    resolver: Callable[[str, str], Awaitable[SandboxScope | None]] | None,
) -> None:
    """Bind the (single) session→scope resolver — ``(user_id, session_id) ->
    SandboxScope | None``. Bound at boot by the tasks module so task sessions
    route to their task's sandbox; ``None`` unbinds (tests)."""
    global _scope_resolver  # noqa: PLW0603
    _scope_resolver = resolver


def _scope_cache_put(session_id: str, scope: SandboxScope) -> None:
    if len(_scope_cache) >= _SCOPE_CACHE_MAX:
        # Bounded: drop an arbitrary ~eighth. Scopes re-derive cheaply.
        for key in list(_scope_cache)[: _SCOPE_CACHE_MAX // 8]:
            _scope_cache.pop(key, None)
    _scope_cache[session_id] = scope


async def _scope_for(user_id: str, session_id: str) -> SandboxScope:
    """The sandbox scope serving ``session_id`` (cached; resolver-aware)."""
    cached = _scope_cache.get(session_id)
    if cached is not None:
        return cached
    scope: SandboxScope | None = None
    if _scope_resolver is not None:
        try:
            scope = await _scope_resolver(user_id, session_id)
        except Exception:  # noqa: BLE001 — resolver failure degrades to session scope
            logger.debug("sandbox scope resolver failed for %s", session_id, exc_info=True)
    if scope is None:
        scope = SandboxScope(kind="session", id=session_id)
    _scope_cache_put(session_id, scope)
    return scope


def _accepts_scope(fn: Any) -> bool:
    """Whether an allocator method takes the (additive) ``scope`` kwarg.

    Allocators written against the pre-scope port signature keep working —
    they are simply never handed a scope (owner-singleton semantics)."""
    import inspect

    try:
        return "scope" in inspect.signature(fn).parameters
    except (TypeError, ValueError):  # builtins / exotic callables
        return False


async def _kernel_for(user_id: str, scope: SandboxScope | None = None) -> KernelClient:
    """Resolve the execution kernel client for ``user_id`` via the allocator."""
    from valuz_agent.ports.extensions import ext

    alloc = getattr(ext, "sandbox_allocator", None)
    if alloc is None:
        return client  # no allocator bound → process-global client (current behavior)
    if scope is not None and _accepts_scope(alloc.ensure):
        lease = await alloc.ensure(owner_user_id=user_id, scope=scope)
    else:
        lease = await alloc.ensure(owner_user_id=user_id)
    if lease is None or lease.endpoint is None:
        return client  # "use the process/global client" (BootSingletonAllocator default)
    ep = lease.endpoint
    cached = _endpoint_clients.get(ep.base_url)
    if cached is None:
        from valuz_agent.adapters.kernel_client_http import HttpKernelClient

        cached = HttpKernelClient(ep.base_url, token=ep.token)
        _endpoint_clients[ep.base_url] = cached
    return cached


async def _kernel_for_existing(
    user_id: str, scope: SandboxScope | None = None
) -> KernelClient | None:
    """Resolve the owner's EXISTING kernel for a live tap — never provisions.

    Used by GLOBAL-LIVE (``subscribe_all_events``): opening the decision inbox
    must not spin up a sandbox. Returns ``None`` when the owner has no live
    kernel (caller relies on the durable snapshot). No allocator, or a
    boot-singleton lease (``endpoint=None``) → the process-global ``client``
    (local single-user, in-process kernel) — behavior unchanged.
    """
    from valuz_agent.ports.extensions import ext

    alloc = getattr(ext, "sandbox_allocator", None)
    if alloc is None:
        return client
    peek = getattr(alloc, "peek", None)
    if peek is None:
        return client  # allocator predates the peek seam → best-effort global client
    if scope is not None and _accepts_scope(peek):
        lease = await peek(owner_user_id=user_id, scope=scope)
    else:
        lease = await peek(owner_user_id=user_id)
    if lease is None:
        return None  # no live kernel for this owner → no live tap
    if lease.endpoint is None:
        return client  # boot-singleton default → process-global client
    ep = lease.endpoint
    cached = _endpoint_clients.get(ep.base_url)
    if cached is None:
        from valuz_agent.adapters.kernel_client_http import HttpKernelClient

        cached = HttpKernelClient(ep.base_url, token=ep.token)
        _endpoint_clients[ep.base_url] = cached
    return cached


async def create_session(
    user_id: str, req: CreateSessionRequest, *, scope: SandboxScope | None = None
) -> SessionData:
    # Scope precedence: explicit (tasks pass ``task:{task_id}`` — the
    # ``valuz_task_session`` row does not exist yet at creation time, so the
    # resolver can't see it) → derived from the host-preminted ``req.id`` →
    # None (owner-singleton) when the caller let the kernel mint the id.
    req_id = getattr(req, "id", None)
    if scope is None and req_id:
        scope = await _scope_for(user_id, req_id)
    elif scope is not None and req_id:
        _scope_cache_put(req_id, scope)
    return await (await _kernel_for(user_id, scope)).create_session(user_id, req)


async def runtime_availability() -> dict[str, RuntimeAvailability]:
    """Per-runtime availability from the process-global kernel client.

    Host-scoped (no ``user_id``) — routes to the process/boot-attached kernel:
    in-process for the bundled desktop (local-host probe), the boot sandbox when
    one is attached. A per-user execution kernel is an overlay concern (§8)."""
    return await client.runtime_availability()


async def bg_busy_session_ids() -> list[str]:
    """Sessions whose warm runtime carries a live background task (process-
    scoped, id-only — intersect with an owner-scoped session set)."""
    return await client.bg_busy_session_ids()


async def get_session(user_id: str, session_id: str) -> SessionData | None:
    return await client.get_session(user_id, session_id)


async def list_sessions(
    user_id: str,
    *,
    status: str | None = None,
    ids: list[str] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[SessionData]:
    return await client.list_sessions(user_id, status=status, ids=ids, limit=limit, offset=offset)


async def list_all_sessions(
    *,
    status: str | None = None,
    ids: list[str] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[SessionData]:
    """Cross-owner session list — startup recovery + host aggregators only.
    Every request-serving caller uses ``list_sessions(user_id, ...)``."""
    return await client.list_all_sessions(status=status, ids=ids, limit=limit, offset=offset)


async def update_session(user_id: str, session_id: str, req: UpdateSessionRequest) -> SessionData:
    return await client.update_session(user_id, session_id, req)


async def delete_session(user_id: str, session_id: str) -> bool:
    return await client.delete_session(user_id, session_id)


async def set_mode(user_id: str, session_id: str, mode: str) -> SessionData:
    return await client.set_mode(user_id, session_id, mode)


async def finalize_session(
    user_id: str, session_id: str, req: FinalizeSessionRequest
) -> SessionData:
    return await client.finalize_session(user_id, session_id, req)


async def append_event(user_id: str, session_id: str, event: EventPayload) -> bool:
    return await client.append_event(user_id, session_id, event)


async def emit_live_event(user_id: str, session_id: str, type: str, data: dict[str, Any]) -> None:
    # Live-only broadcast: with no live kernel there is nobody to receive it —
    # peek (never provision) and no-op. Provisioning a sandbox just to emit an
    # ephemeral frame was pure waste (and, under scoped allocation, would spin
    # an instance on every host-side emit for an idle session).
    k = await _kernel_for_existing(user_id, await _scope_for(user_id, session_id))
    if k is None:
        return
    await k.emit_live_event(user_id, session_id, type, data)


async def get_events(
    user_id: str,
    session_id: str,
    *,
    limit: int = 200,
    offset: int = 0,
    after_seq: int | None = None,
) -> list[EventData]:
    return await client.get_events(
        user_id, session_id, limit=limit, offset=offset, after_seq=after_seq
    )


async def get_events_window(
    user_id: str, session_id: str, *, before_seq: int | None = None, turn_limit: int = 20
) -> EventWindowData:
    return await client.get_events_window(
        user_id, session_id, before_seq=before_seq, turn_limit=turn_limit
    )


async def subscribe_session_events(user_id: str, session_id: str) -> AsyncIterator[EventData]:
    k = await _kernel_for(user_id, await _scope_for(user_id, session_id))
    async for event in k.subscribe_session_events(user_id, session_id):
        yield event


async def subscribe_session_events_existing(
    user_id: str, session_id: str
) -> AsyncIterator[EventData]:
    """Live tap on ONE session's stream via the EXISTING kernel — never provisions.

    The SSE adapter uses this so that opening a (historical) conversation never
    spins up a sandbox: history is served from the durable store; the live tap
    only attaches when the session's kernel is already running (the adapter
    re-peeks periodically to catch a kernel that comes up mid-stream). Yields
    nothing when there is no live kernel for the session's scope.
    """
    k = await _kernel_for_existing(user_id, await _scope_for(user_id, session_id))
    if k is None:
        return
    async for event in k.subscribe_session_events(user_id, session_id):
        yield event


def subscribe_all_events(
    types: tuple[str, ...] | None = None,
) -> AsyncIterator[EventData]:
    """Process-global live tap (all sessions of the process/boot kernel).

    Unchanged: used by the decision aggregator in LOCAL / single-kernel mode.
    Multi-tenant hosts use :func:`subscribe_all_events_for` instead.
    """
    return client.subscribe_all_events(types)


async def subscribe_all_events_for(
    user_id: str, types: tuple[str, ...] | None = None
) -> AsyncIterator[EventData]:
    """Live tap on ONE owner's cross-session event stream (GLOBAL-LIVE, remote).

    Routed to that owner's EXISTING kernel via ``_kernel_for_existing`` (never
    provisions). A multi-tenant host runs one kernel per owner, so that kernel's
    "all events" stream IS the owner's cross-session stream. Yields nothing when
    the owner has no live kernel — callers rely on the durable snapshot.
    ``types`` is an optional event-type allowlist, filtered at the source
    (in-process: before translation; remote: server-side via ``?types=``).
    """
    k = await _kernel_for_existing(user_id)
    if k is None:
        return
    async for event in k.subscribe_all_events(types):
        yield event


async def usage_rollup(user_id: str, start_ms: int, end_ms: int) -> list[UsageRollupData]:
    return await client.usage_rollup(user_id, start_ms, end_ms)


async def list_messages(
    user_id: str, session_id: str, *, limit: int = 50, offset: int = 0
) -> list[MessageData]:
    return await client.list_messages(user_id, session_id, limit=limit, offset=offset)


async def latest_message_id(user_id: str, session_id: str) -> str | None:
    messages = await client.list_messages(user_id, session_id, limit=1)
    return messages[0].id if messages else None


async def submit_action(user_id: str, session_id: str, req: SubmitActionRequest) -> dict[str, Any]:
    k = await _kernel_for(user_id, await _scope_for(user_id, session_id))
    return await k.submit_action(user_id, session_id, req)


async def interrupt(user_id: str, session_id: str) -> None:
    k = await _kernel_for(user_id, await _scope_for(user_id, session_id))
    await k.interrupt(user_id, session_id)


async def run_turn(
    user_id: str,
    session_id: str,
    text: str,
    attachments: list[dict[str, Any]] | None = None,
    additional_context: str = "",
) -> MessageData:
    k = await _kernel_for(user_id, await _scope_for(user_id, session_id))
    return await k.run_turn(user_id, session_id, text, attachments, additional_context)


async def scan_orphan_pendings() -> int:
    return await client.scan_orphan_pendings()  # type: ignore[attr-defined]


async def scan_orphan_runs() -> int:
    return await client.scan_orphan_runs()  # type: ignore[attr-defined]


async def cleanup_runtime(session_id: str) -> None:
    await client.cleanup_runtime(session_id)  # type: ignore[attr-defined]
