"""Boot-time session recovery — clear stranded ``running`` rows.

When the host process dies mid-turn (crash, SIGKILL, hard restart),
kernel ``sessions`` rows that were ``status="running"`` at the time
stay that way forever in the DB. The next time a user tries to send a
message, ``SessionService.send_message`` short-circuits with a 409
``Session is already running`` and they're stuck.

This module provides a single function, ``recover_running_sessions``,
called from ``api/app.py``'s startup chain after the kernel has come
back up. Its contract:

- Find every kernel session row whose ``status == "running"``.
- Mark it ``terminated`` with a ``stop_reason`` that records the
  recovery event so SSE replay shows a clear failure rather than a
  silent hang.
- Append a ``session_error`` event into the kernel events table so
  any client that reconnects with ``after_seq`` sees the explanation.

The agent turn itself can't be resumed — its in-process orchestrator
state died with the previous process. Cleanly marking the session
``terminated`` is the most we can do without the user re-issuing the
prompt.

This is conservative on purpose: we never touch ``idle`` / ``created``
rows, so a session legitimately running in another worker (in some
future multi-process deployment) wouldn't be racey-killed. Today the
host is single-process, so any ``running`` row at startup is by
definition stranded.
"""

from __future__ import annotations

import logging

from valuz_agent.adapters import kernel_client
from valuz_agent.adapters.data_reader import data_reader

logger = logging.getLogger(__name__)


async def recover_running_sessions(*, batch_limit: int = 500) -> int:
    """Scan for stranded running sessions and finalise them.

    Returns the number of sessions transitioned to terminated. Logs
    each recovery so operators can audit a noisy restart.

    Failures inside the loop are caught per-session — one bad row
    must not stop the rest from being recovered. The function never
    raises; the caller (startup hook) treats it as best-effort.
    """
    try:
        # Cross-owner startup sweep — finalise every owner's stranded sessions.
        sessions = await data_reader().list_all_sessions(limit=batch_limit)
    except Exception:  # noqa: BLE001 — startup must not block on bookkeeping
        logger.exception("recover_running_sessions: failed to list kernel sessions")
        return 0

    recovered = 0
    for session in sessions:
        if session.status != "running":
            continue
        try:
            await _finalise_one(session)
            recovered += 1
        except Exception:  # noqa: BLE001
            logger.exception(
                "recover_running_sessions: failed to finalise session %s",
                session.id,
            )

    if recovered:
        logger.warning(
            "recover_running_sessions: marked %d stranded session(s) as terminated",
            recovered,
        )
    return recovered


async def _finalise_one(session: object) -> None:
    """Flip one session from ``running`` to ``terminated`` + emit an event.

    Goes through the kernel client's finalize endpoint, which applies the
    status flip and appends the explanatory ``session_error`` event in one
    supervisor call (the event is anchored onto the session's latest
    message; dropped when the session never ran a turn — there is nothing
    for SSE to replay anyway).
    """
    from app.schemas import (
        EventPayload,
        FinalizeSessionRequest,
    )

    sid = session.id  # type: ignore[attr-defined]
    owner = session.user_id  # type: ignore[attr-defined]

    await kernel_client.finalize_session(
        owner,
        sid,
        FinalizeSessionRequest(
            status="terminated",
            error_event=EventPayload(
                type="session_error",
                data={
                    "category": "ServerRestart",
                    "message": "Agent turn was interrupted by a server restart.",
                },
            ),
        ),
    )
    logger.info("Recovered stranded session %s → terminated", sid)


async def resume_queued_drains() -> int:
    """Resume host-driven queue drains after a restart (durable-queue §9 ②).

    The input queue is persisted, so a restart preserves queued follow-ups.
    ``recover_running_sessions`` (① above) already terminated any session that
    was mid-turn at crash time; this step picks up the remaining alive sessions
    that still have ``queued`` items and re-kicks their drain so a long-running
    workflow's follow-ups continue without the user re-issuing them.

    Conservative on purpose:
    - Runs under each session's own owner context (drain reads are owner-scoped).
    - Skips paused queues (an interrupt soft-pause survives restart — the user
      resumes explicitly).
    - Only re-kicks **alive** sessions (``idle`` / ``created``). Items on a
      session terminated by ① stay ``queued`` and drain on the user's next
      interaction rather than auto-running onto a dead turn here.

    Best-effort; never raises (boot must not block).
    """
    from valuz_agent.infra.auth_context import reset_current_user_id, set_current_user_id
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.infra.eventbus import event_bus
    from valuz_agent.modules.sessions import project_index
    from valuz_agent.modules.sessions.datastore import SessionDatastore
    from valuz_agent.modules.sessions.mappers import _map_kernel_status
    from valuz_agent.modules.sessions.run_orchestrator import schedule_drain

    try:
        async with async_unit_of_work(commit=False) as db:
            pairs = await SessionDatastore(db).list_queued_session_owners()
    except Exception:  # noqa: BLE001 — startup must not block on bookkeeping
        logger.exception("resume_queued_drains: failed to list queued sessions")
        return 0

    resumed = 0
    for session_id, owner in pairs:
        token = set_current_user_id(owner)
        try:
            if await project_index.get_queue_paused_at(session_id) is not None:
                continue
            session = await kernel_client.get_session(owner, session_id)
            status = _map_kernel_status(session.status) if session else None
            if status in ("idle", "created"):
                schedule_drain(session_id, event_bus)
                resumed += 1
        except Exception:  # noqa: BLE001 — one bad session must not stop the rest
            logger.exception("resume_queued_drains: failed for session %s", session_id)
        finally:
            reset_current_user_id(token)

    if resumed:
        logger.info("resume_queued_drains: re-kicked %d queued session drain(s)", resumed)
    return resumed


__all__ = ["recover_running_sessions", "resume_queued_drains"]
