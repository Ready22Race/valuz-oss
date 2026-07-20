"""Stranded-session reset semantics — pure functions over an explicit StorePort.

The kernel owns WHAT a reset means; the caller owns WHICH store it applies to:

- the kernel's own boot scans apply it to the runtime sqlite (own lineage);
- the HOST's liveness-driven recovery applies it to the DataService durable —
  the session being reset lived in a sandbox that is gone, so its runtime
  store is unreachable and the durable copy is what the host repairs.

The explicit ``store`` parameter IS the plane selection — there is no ambient
store, so a call site always reads which copy it is repairing.
"""

from __future__ import annotations

from src.core.events import Event
from src.core.store_port import StorePort
from src.core.time_utils import now_ms
from src.core.types import Error, Session


async def seal_session_pendings(store: StorePort, user_id: str, session_id: str) -> int:
    """Seal one session's still-open ``requires_action`` events with a synthetic
    ``action_resolved(decision="expired", resolved_by="system")``.

    Pending approvals do not survive their process (design doc §6.3) — the
    contract is uniform across runtimes. Returns the number sealed."""
    events = await store.get_events(
        user_id,
        session_id,
        types=("requires_action", "action_resolved"),
        limit=1000,
    )
    open_pendings: dict[str, str] = {}  # pending_id → message_id
    for ev in events:
        pid = ev.data.get("pending_id")
        if not isinstance(pid, str):
            continue
        if ev.type == "requires_action":
            msg_id = ev.data.get("message_id")
            if isinstance(msg_id, str):
                open_pendings.setdefault(pid, msg_id)
        elif ev.type == "action_resolved":
            open_pendings.pop(pid, None)
    for pid, msg_id in open_pendings.items():
        await store.append_event(
            user_id,
            session_id,
            msg_id,
            Event(
                type="action_resolved",
                data={
                    "pending_id": pid,
                    "decision": "expired",
                    "resolved_by": "system",
                },
            ),
        )
    return len(open_pendings)


async def reset_stranded(store: StorePort, session: Session) -> None:
    """Reset one stranded ``running`` session: idle + resumable ``host_restart``
    stop reason (task recovery's ``classify_member`` maps it to ``resume``),
    running messages → errored (spinner fix)."""
    session.status = "idle"
    session.stop_reason = Error(
        category="host_restart",
        retry_status="terminal",
        message="host process restarted while turn was in flight",
    )
    await store.save_session(session)
    messages = await store.list_messages_for_session(session.user_id, session.id)
    now = now_ms()
    for m in messages:
        if m.status != "running":
            continue
        m.status = "errored"
        m.error_message = {
            "category": "host_restart",
            "message": "host process restarted while message was in flight",
        }
        m.ended_at = now
        await store.save_message(session.user_id, m)


async def reset_stranded_session(store: StorePort, user_id: str, session_id: str) -> bool:
    """Per-session stranded reset (the HOST-driven, liveness-checked path).

    The HOST — the only party that can check whether a session's sandbox is
    still alive (``ext.sandbox_allocator.peek``) — decides WHICH sessions are
    stranded and calls this per session against its durable store. The full
    reset: seal open pendings, stamp idle + resumable ``host_restart``, flip
    running messages to errored. No-op (returns ``False``) unless the session
    exists and is still ``running``.
    """
    session = await store.load_session(user_id, session_id)
    if session is None or session.status != "running":
        return False
    await seal_session_pendings(store, user_id, session_id)
    await reset_stranded(store, session)
    return True
