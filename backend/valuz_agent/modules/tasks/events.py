"""Host-side event-bus topics for the tasks module.

ADR-001 additive contract: the event NAME and payload FIELD NAMES are the
frozen surface commercial overlays subscribe to (an overlay mirrors the string
rather than importing this module — keep both in sync).
"""

from __future__ import annotations

import logging
from typing import Any

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
    db: Any,
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
