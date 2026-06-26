"""Task trigger provenance — classify *what spawned a task*.

Resolved once at kickoff/draft and persisted immutably on ``TaskRow`` (the
``trigger_*`` columns). Surfaced in the task list ("由 … 触发") and lets the
reverse "what did X spawn?" query run off the indexed source ids.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.modules.tasks.datastore import TaskSessionDatastore


@dataclass(frozen=True)
class TriggerProvenance:
    """Typed answer to "who/what triggered this task?"."""

    trigger_type: str  # user | chat | agent | automation
    trigger_task_id: str | None = None
    trigger_agent_slug: str | None = None
    trigger_automation_id: str | None = None


async def resolve_trigger_provenance(
    db: AsyncSession,
    *,
    originating_session_id: str | None,
    trigger_type: str | None = None,
    trigger_automation_id: str | None = None,
) -> TriggerProvenance:
    """Classify a task's trigger source.

    Precedence:
      1. ``trigger_type="automation"`` (the automation runner passes it with the
         automation id) — taken as-is; an automation has no originating session.
      2. an ``originating_session_id`` that belongs to a task lead/member run →
         ``agent`` + that parent task + the member's agent slug.
      3. an ``originating_session_id`` that is a plain project conversation →
         ``chat`` (the session itself is the link, kept on
         ``metadata.originating_session_id``).
      4. neither → ``user`` (a direct user action).
    """
    if trigger_type == "automation":
        return TriggerProvenance("automation", trigger_automation_id=trigger_automation_id)

    if originating_session_id:
        run = await TaskSessionDatastore(db).get_run(originating_session_id)
        if run is not None and run.task_id:
            return TriggerProvenance(
                "agent",
                trigger_task_id=run.task_id,
                trigger_agent_slug=run.agent_slug,
            )
        return TriggerProvenance("chat")

    return TriggerProvenance("user")


__all__ = ["TriggerProvenance", "resolve_trigger_provenance"]
