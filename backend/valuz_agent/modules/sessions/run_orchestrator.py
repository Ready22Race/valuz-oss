"""Background agent-turn execution + post-turn finalization.

Drives one turn through the shared :func:`ActorRunner.run_session_to_idle`
runtime primitive (ADR-023 AC#5 — ONE turn-to-idle engine for both chat
sessions and task members/leads), adding only the chat-path billing meter via
an ``on_message`` hook. Post-turn finalization (``_finalize_session``) stays
here as the single finalize sink that the runtime primitive imports and calls.
Split out of ``service`` so the god module keeps only the SessionService
surface; the task orchestrator reuses ``_finalize_session`` directly.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

# Side-effect: puts the kernel on sys.path so ``src.core`` / ``app.*``
# resolve at call time.
import valuz_agent.boot.kernel  # noqa: F401
from valuz_agent.adapters import kernel_client
from valuz_agent.infra.auth_context import require_current_user_id
from valuz_agent.infra.eventbus import EventBus
from valuz_agent.modules.tasks.actor_runner import run_session_to_idle

logger = logging.getLogger(__name__)

# Sessions with a queue-drain chain currently in flight. Single-flights the
# drain (an idle-kick enqueue must not race the post-turn drain) AND doubles as
# a host-side "busy" signal so ``send_message`` 409s during the brief idle
# windows *between* drained items (see ``is_draining_queue``). In-memory by
# design — the queue itself is durable (DB), the drain task is transient.
_active_drains: set[str] = set()


def is_draining_queue(session_id: str) -> bool:
    """True while a queue-drain chain is in flight for this session.

    ``send_message`` treats this like ``status=="running"`` so a new turn can't
    slip into the sub-second idle gap between two drained queue items.
    """
    return session_id in _active_drains


def _chat_billing_meter(session_id: str) -> Any:
    """Build the chat-path billing meter callback for a session.

    Shared by the initial turn and every drained queue item so each metered
    turn bills identically.
    """

    async def _meter(message: Any, after_run: Any) -> None:
        if message.input_tokens is not None or message.output_tokens is not None:
            from valuz_agent.infra.auth_context import get_current_user_id
            from valuz_agent.ports.billing import MeterEvent
            from valuz_agent.ports.extensions import ext

            uid = (after_run.metadata if after_run else {}).get(
                "owner_user_id"
            ) or get_current_user_id()
            try:
                if uid is None:
                    raise LookupError("no owner user_id for billing meter")
                await ext.billing.meter(
                    MeterEvent(
                        user_id=uid,
                        event_type="llm_call",
                        cost_usd=0.0,
                        metadata={
                            "message_id": message.id,
                            "session_id": session_id,
                            "input_tokens": message.input_tokens or 0,
                            "output_tokens": message.output_tokens or 0,
                            "cache_read_tokens": message.cache_read_tokens or 0,
                            "cache_write_tokens": message.cache_write_tokens or 0,
                            "model_usage": message.model_usage,
                        },
                    )
                )
            except Exception:  # noqa: BLE001
                logger.warning("Billing meter failed for session %s", session_id)

    return _meter


async def _run_agent_background(
    session_id: str,
    content: str,
    event_bus: EventBus,
) -> None:
    """Drive one agent turn in the background, then drain any queued follow-ups.

    Thin wrapper over the shared :func:`run_session_to_idle` runtime primitive
    (ADR-023): the primitive owns the attach-sink → build UserMessage →
    run_turn → read-back-status → finalize → consume-attachments →
    detach/cleanup → publish SESSION_FINISHED shape with the layered failure
    handling that guarantees a session never gets stranded in
    ``status="running"``. This wrapper adds the chat-path billing meter and the
    post-turn queue drain (docs/design/session-input-queue.md).
    """
    meter = _chat_billing_meter(session_id)
    await run_session_to_idle(session_id, content, event_bus, on_message=meter)
    await _drain_queue_after_turn(session_id, event_bus, on_message=meter)


async def _drain_queue_after_turn(
    session_id: str,
    event_bus: EventBus,
    on_message: Any | None = None,
) -> None:
    """Run queued follow-up inputs FIFO after a turn finishes (host-driven).

    Per item: bail if the app is shutting down or the queue is paused (an
    interrupt soft-pauses — items stay until an explicit resume); budget
    pre-check (mark ``blocked`` + stop on failure, preserving the 402 path);
    else mark ``dispatched`` and drive it through ``run_session_to_idle`` with
    its frozen attachment snapshot, then loop. Single-flighted via
    ``_active_drains`` so an idle-kick enqueue can't double-run an item.
    """
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.infra.lifecycle import is_draining
    from valuz_agent.modules.sessions import project_index
    from valuz_agent.modules.sessions.datastore import SessionDatastore
    from valuz_agent.modules.sessions.errors import BudgetExceeded
    from valuz_agent.modules.sessions.events import SESSION_FINISHED

    if session_id in _active_drains:
        return
    _active_drains.add(session_id)
    try:
        while True:
            if is_draining():
                return
            if await project_index.get_queue_paused_at(session_id) is not None:
                return

            async with async_unit_of_work(commit=False) as db:
                head = await SessionDatastore(db).peek_next_queued(session_id)
            if head is None:
                return

            head_id = head.id
            payload = head.input or {}
            text = str(payload.get("text") or "")
            attachments = list(payload.get("attachments") or [])

            session = await kernel_client.get_session(require_current_user_id(), session_id)
            if session is None:
                return

            try:
                from valuz_agent.modules.sessions.service import _enforce_budget

                await _enforce_budget(session)
            except BudgetExceeded as exc:
                async with async_unit_of_work() as db:
                    await SessionDatastore(db).mark_queued_status(
                        head_id,
                        "blocked",
                        error_message=getattr(exc, "message_key", None) or str(exc),
                    )
                # Surface the stall: followers refetch the queue on a finish.
                event_bus.publish(SESSION_FINISHED, session_id=session_id, status="idle")
                return

            async with async_unit_of_work() as db:
                await SessionDatastore(db).mark_queued_status(head_id, "dispatched")

            await run_session_to_idle(
                session_id,
                text,
                event_bus,
                on_message=on_message,
                queued_attachments=attachments,
            )
    finally:
        _active_drains.discard(session_id)


def schedule_drain(session_id: str, event_bus: EventBus) -> None:
    """Spawn a background queue drain for an idle session (idle-kick / resume).

    ``asyncio.create_task`` copies the current contextvars, so the ambient
    request owner (``require_current_user_id``) propagates into the drain.
    A no-op if a drain is already in flight for the session.
    """
    if session_id in _active_drains:
        return
    meter = _chat_billing_meter(session_id)
    asyncio.create_task(_drain_queue_after_turn(session_id, event_bus, on_message=meter))


# Strip leading skill-trigger tokens (``/<slug>``) when deriving a
# session title from the user's first message. Composer ships these
# inline as part of the prompt, but they're routing metadata — using
# "/stock-screener 找股票" as the chat title leaks scaffolding into
# the sidebar. Only the *prefix* is stripped; ``/`` mentions later
# in the prose are kept verbatim because they're presumably part of
# the intent. (CN-IME ``、`` is normalized to ``/`` in the Composer
# before send, so we only need to handle the canonical form here.)
_SKILL_PREFIX_RE = re.compile(r"^\s*(?:/[a-zA-Z0-9_-]+\s+)+")


def _derive_session_name(content: str) -> str:
    cleaned = _SKILL_PREFIX_RE.sub("", content)
    return cleaned[:40].replace("\n", " ").strip()


async def _finalize_session(
    session_id: str,
    content: str,
    final_status: str,
    error: BaseException | None = None,
) -> None:
    """Persist post-turn valuz metadata and the resolved kernel status.

    Split out so both the success and failure paths in ``_run_agent_background``
    can share it. Builds a fresh ``Session`` dataclass because the kernel's
    types are frozen.

    When ``error`` is supplied (the turn raised), a ``session_error`` event is
    appended **durably** as part of the same finalize call — the live
    ``emit_live_event`` only reaches SSE followers connected at the moment of
    failure, so without this the actual reason is lost on reload and the UI
    shows a bare "Run failed". ``stop_reason_*`` mark the terminal state as an
    error rather than a clean idle.
    """
    session = await kernel_client.get_session(require_current_user_id(), session_id)
    if session is None:
        return

    meta = dict(session.metadata)
    valuz = dict(meta.get("valuz") or {})
    valuz["last_user_message_text"] = content
    if not valuz.get("name"):
        valuz["name"] = content[:40].replace("\n", " ").strip()
    meta["valuz"] = valuz

    from app.schemas import EventPayload, FinalizeSessionRequest

    error_event = None
    stop_reason_type = None
    stop_reason_message = None
    if error is not None:
        message = str(error) or "agent turn failed"
        error_event = EventPayload(
            type="session_error",
            data={"category": type(error).__name__, "message": message},
        )
        stop_reason_type = "error"
        stop_reason_message = message

    await kernel_client.finalize_session(
        require_current_user_id(),
        session_id,
        FinalizeSessionRequest(
            status=final_status,  # type: ignore[arg-type]
            metadata=meta,
            error_event=error_event,
            stop_reason_type=stop_reason_type,  # type: ignore[arg-type]
            stop_reason_message=stop_reason_message,
        ),
    )

    # Arm the idle memory-extraction trigger (memory-system-design §7.1). The
    # scheduler debounces, so it fires once the conversation goes quiet — not per
    # turn — and is a no-op until the runner is wired at boot. Never blocks a turn.
    try:
        from valuz_agent.modules.memory.scheduler import idle_scheduler

        idle_scheduler.notify_turn(session_id, require_current_user_id())
    except Exception:  # noqa: BLE001 — memory triggering must never fail a turn
        logger.debug("memory idle trigger skipped for %s", session_id, exc_info=True)
