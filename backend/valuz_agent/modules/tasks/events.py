"""The tasks module's EVENT WRITE surface — timeline rows + bus topics.

Two things live here, both "something happened to this task, record it":

  * :func:`finalize_task` — the composed terminal write (status flip + bus
    announce + terminal timeline event). Runs on the caller's unit of work.
  * :func:`record_awaiting_user` / :func:`record_user_answered` — timeline
    rows projected from the cross-cutting Decision Inbox. These open their own
    unit of work (their caller, ``modules/decisions/aggregator.py``, has no
    task transaction to join).

ADR-001 additive contract: the bus event NAME and payload FIELD NAMES are the
frozen surface commercial overlays subscribe to (an overlay mirrors the string
rather than importing this module — keep both in sync).

Not here: mailbox DELIVERY (lead↔member text, chat→task inject) — that is
``tasks/messaging.py``. The split is "does it put something in a mailbox?".
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.tasks.datastore import TaskDatastore, TaskEventDatastore

logger = logging.getLogger(__name__)

# Published (best-effort) whenever a task reaches a TERMINAL status —
# ``completed`` / ``stopped`` / ``blocked`` — from every terminal write site
# (finish_task, auto-finalize, sync-kickoff failure, health monitor). Payload:
# ``task_id``, ``owner_user_id``, ``status``. First consumer: the commercial
# sandbox allocator clamps the ``task:{id}`` scope sandbox's TTL so a finished
# task's instance is reclaimed after a short grace instead of lingering for the
# full active window (24h under the platform-TTL lease model).
TASK_FINALIZED = "task.finalized"


def publish_task_finalized(task_id: str, owner_user_id: str, status: str) -> None:
    """Announce a task's terminal status on the in-process bus.

    Best-effort by contract: subscribers run synchronously on the global bus,
    so this must never let a subscriber error (or a missing bus) affect task
    finalization.

    Prefer :func:`finalize_task` — it composes this announce with the status
    flip and the terminal event so no write site can ship a partial terminal.
    """
    try:
        from valuz_agent.infra.eventbus import event_bus

        event_bus.publish(
            TASK_FINALIZED, task_id=task_id, owner_user_id=owner_user_id, status=status
        )
    except Exception:  # noqa: BLE001 — never let a subscriber break finalize
        logger.debug("task.finalized publish failed for %s", task_id, exc_info=True)


async def finalize_task(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    task_id: str,
    status: str,
    event_type: str,
    actor: str,
    session_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> Any:  # returns the appended TaskEventRow
    """THE terminal write — every site that ends a task goes through here.

    Composes the three legs no terminal site may split again (the 2026-07
    fossil-edit bugs were exactly a missing leg: explicit ``finish_task``
    shipped without the announce, ``stop_task`` without announce *or* the
    state-machine guard, the lead-turn-error block without the announce):

      1. the status flip through the ``task_state`` guard
         (``TaskDatastore.update_task_status``),
      2. the ``task.finalized`` announce (the commercial sandbox allocator's
         TTL clamp listens on it — see :data:`TASK_FINALIZED`),
      3. the terminal task event row (returned, so callers can hang
         notifications off its id).

    "Composes" means ONE CALL SITE, not one transaction. The three legs are
    not atomic: ``update_task_status`` and ``append_event`` each commit on
    their own (every task datastore method does), so the caller's
    ``async_unit_of_work`` is a session scope, not a transaction, and the
    announce in between is an in-process bus publish that cannot be rolled
    back at all. A crash between legs 1 and 3 leaves a task whose status is
    terminal with no terminal event on its timeline.

    The value here is that no site can FORGET a leg — which is what actually
    went wrong (see above) — not that the three land or fail together. Making
    them truly atomic means changing the datastore-commits-itself convention
    repo-wide; until then, readers must tolerate a terminal status without its
    event (the task detail page already falls back to the status).

    Runs on the caller's unit of work; the announce is best-effort and
    synchronous (see :func:`publish_task_finalized`).
    """
    await TaskDatastore(db).update_task_status(user_id, task_id, status)
    publish_task_finalized(task_id, user_id, status)
    return await TaskEventDatastore(db).append_event(
        user_id,
        project_id=project_id,
        task_id=task_id,
        type=event_type,
        actor=actor,
        session_id=session_id,
        payload=payload or {},
    )


async def block_task(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    task_id: str,
    event_type: str,
    actor: str,
    reason: str,
    session_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> Any:  # returns the appended TaskEventRow
    """Put a task into ``blocked`` AND raise the user-facing notification.

    ``blocked`` is the module's single "needs your attention" state — a task
    reaches it only when something went wrong and a human has to decide what
    happens next. That makes the notification part of the transition, not an
    optional extra: a blocked task nobody is told about is a task that silently
    stops.

    Every ``blocked`` write went through :func:`finalize_task` and then
    hand-wrote the same notification call (kickoff credential failure, lead
    turn error, unresolved subtasks, health-monitor zombie sweep — four sites,
    four copies, four chances to forget). This composes them.

    ``reason`` is the human-readable line shown in the notification; it is also
    folded into the event payload so the timeline and the notification cannot
    disagree about why.
    """
    from valuz_agent.modules.notifications.projectors import (
        record_task_failure_notification,
    )

    event = await finalize_task(
        db,
        user_id=user_id,
        project_id=project_id,
        task_id=task_id,
        status="blocked",
        event_type=event_type,
        actor=actor,
        session_id=session_id,
        payload={**(payload or {}), "error": reason},
    )
    await record_task_failure_notification(
        task_id=task_id,
        project_id=project_id,
        event_id=event.id,
        event_type=event_type,
        reason=reason,
        user_id=user_id,
    )
    return event


# ---------------------------------------------------------------------------
# Subtask outcome events
#
# Both of these were emitted from more than one place with a DIFFERENT payload
# each time. ``subtask_failed`` had three shapes — the heartbeat backstop wrote
# {agent_name, subtask_key, status, summary, reason}, dispatch wrote {agent,
# agent_name, status, error} with no key and no summary, and the actor-loop
# finalize spread a whole manifest. The timeline renderer falls back to a
# ``text|summary|goal|error`` lookup for these types, so the detail line a user
# saw depended on which internal path had failed, and no consumer could rely on
# ANY field being present.
#
# One emitter each, every key always populated. Add a field here, not at a call
# site.
# ---------------------------------------------------------------------------


async def record_subtask_failed(
    event_ds: TaskEventDatastore,
    *,
    user_id: str,
    project_id: str,
    task_id: str,
    session_id: str | None,
    agent_slug: str,
    agent_name: str | None,
    subtask_key: str | None,
    summary: str,
    reason: str,
    artifacts: list[Any] | None = None,
) -> None:
    """A member run ended in failure.

    ``reason`` is the MACHINE-readable cause — which path detected it
    (``dispatch_failed`` / ``heartbeat_detected`` / ``run_error``); ``summary``
    is the human line the timeline shows.
    """
    await event_ds.append_event(
        user_id,
        project_id=project_id,
        task_id=task_id,
        type="subtask_failed",
        actor=agent_slug,
        session_id=session_id,
        payload={
            "agent": agent_slug,
            "agent_name": agent_name,
            "subtask_key": subtask_key,
            "status": "failed",
            "summary": summary,
            "reason": reason,
            "artifacts": artifacts or [],
        },
    )


async def record_subtask_stopped(
    event_ds: TaskEventDatastore,
    *,
    user_id: str,
    project_id: str,
    task_id: str,
    session_id: str | None,
    agent_slug: str,
    agent_name: str | None,
    subtask_key: str | None,
) -> None:
    """A member run was stopped by the user — not a failure.

    ``actor`` is ``"user"`` rather than the agent: the timeline renders this
    amber-not-red precisely because a person chose it.
    """
    await event_ds.append_event(
        user_id,
        project_id=project_id,
        task_id=task_id,
        type="subtask_stopped",
        actor="user",
        session_id=session_id,
        payload={
            "agent": agent_slug,
            "agent_name": agent_name,
            "subtask_key": subtask_key,
        },
    )


# ---------------------------------------------------------------------------
# Decision-Inbox projections
#
# The Decision Inbox is a cross-cutting overlay keyed by ``task_id``: a pending
# question blocks the agent's turn but leaves NO trace on the task's own
# timeline. These two writes are that trace. Their only caller is
# ``modules/decisions/aggregator.py``; they open their own unit of work because
# it has no task transaction for them to join.
#
# They lived in ``tasks/messaging.py`` until 2026-07 — misfiled, since neither
# delivers anything to a mailbox.
# ---------------------------------------------------------------------------


async def record_awaiting_user(
    *,
    task_id: str,
    project_id: str,
    session_id: str,
    subtask_key: str | None,
    agent_slug: str,
    agent_name: str | None,
    question: str,
    pending_id: str,
    user_id: str,
) -> None:
    """Append an ``awaiting_user`` task event when an agent (lead or member)
    raises a question through the Decision Inbox.

    Without this the task page shows "Running" while the task is actually
    blocked on the user. We do NOT add an ``awaiting_user`` task *status* (the
    task genuinely is still active, and a status would need racy atomic
    clearing on answer) — this event is the timeline record + the SSE frame the
    attention surfaces drive from. Deduped by ``pending_id`` at the caller (the
    aggregator tracks emitted ids per process).
    """
    async with async_unit_of_work() as db:
        await TaskEventDatastore(db).append_event(
            user_id,
            project_id=project_id,
            task_id=task_id,
            type="awaiting_user",
            actor=agent_slug,
            session_id=session_id,
            payload={
                "agent_name": agent_name,
                "question": question,
                "pending_id": pending_id,
                **({"subtask_key": subtask_key} if subtask_key else {}),
            },
        )


async def record_user_answered(
    *,
    task_id: str,
    project_id: str,
    pending_id: str,
    session_id: str | None = None,
    user_id: str,
) -> None:
    """Append a ``user_answered`` task event when a pending question resolves
    (the counterpart to :func:`record_awaiting_user`)."""
    async with async_unit_of_work() as db:
        await TaskEventDatastore(db).append_event(
            user_id,
            project_id=project_id,
            task_id=task_id,
            type="user_answered",
            actor="user",
            session_id=session_id,
            payload={"pending_id": pending_id},
        )
