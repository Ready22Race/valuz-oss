"""Host-side event-bus topics for the tasks module.

ADR-001 additive contract: the event NAME and payload FIELD NAMES are the
frozen surface commercial overlays subscribe to (an overlay mirrors the string
rather than importing this module — keep both in sync).
"""

from __future__ import annotations

import logging

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
    """
    try:
        from valuz_agent.infra.eventbus import event_bus

        event_bus.publish(
            TASK_FINALIZED, task_id=task_id, owner_user_id=owner_user_id, status=status
        )
    except Exception:  # noqa: BLE001 — never let a subscriber break finalize
        logger.debug("task.finalized publish failed for %s", task_id, exc_info=True)
