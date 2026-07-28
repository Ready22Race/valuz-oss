"""TaskService — the HTTP layer's service tier (routes never touch datastores).

Owned reads (incl. the composite detail/timeline reads) + the two intervention
writes (``add_note`` / ``revise_goal`` — the latter must reach a RUNNING lead,
not just the row). Ownership is a parameter, never ambient; methods return
``None`` instead of raising HTTP errors so non-HTTP callers can reuse them.

NOT here: lifecycle orchestration (``task_orchestrator`` services), plan
writes (``plan_commands``), agent-facing reads (``queries`` — loose dicts on
their own UoW for the MCP context).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.modules.tasks.datastore import (
    TaskDatastore,
    TaskEventDatastore,
    TaskSessionDatastore,
)
from valuz_agent.modules.tasks.models import TaskEventRow, TaskRow, TaskSessionRow


@dataclass(frozen=True)
class TaskDetail:
    """A task plus everything the detail page renders in one round trip."""

    task: TaskRow
    runs: list[TaskSessionRow]
    events: list[TaskEventRow]


@dataclass(frozen=True)
class TaskEvents:
    """A task's events plus the task itself.

    The task comes back because the caller needs its ``project_id`` to scope
    the event read — returning it avoids the "load it twice" shape the routes
    had.
    """

    task: TaskRow
    events: list[TaskEventRow]


class TaskService:
    """Owner-scoped task reads + the small intervention writes."""

    def __init__(self, db: AsyncSession) -> None:
        self._tasks = TaskDatastore(db)
        self._runs = TaskSessionDatastore(db)
        self._events = TaskEventDatastore(db)

    # -- single task ---------------------------------------------------

    async def get_owned_task(self, user_id: str, task_id: str) -> TaskRow | None:
        """The caller's task, or ``None``.

        The single most repeated read in the route layer — it precedes almost
        every task mutation as the ownership + existence check.
        """
        return await self._tasks.get_task(user_id, task_id)

    async def get_detail(self, user_id: str, task_id: str) -> TaskDetail | None:
        """Task + runs + timeline for the detail page. ``None`` if not owned."""
        task = await self._tasks.get_task(user_id, task_id)
        if task is None:
            return None
        return TaskDetail(
            task=task,
            runs=await self._runs.list_runs(user_id, task_id),
            events=await self._events.list_events(user_id, task.project_id, task_id),
        )

    async def get_events(self, user_id: str, task_id: str) -> TaskEvents | None:
        """A task's full timeline. ``None`` if the task isn't the caller's."""
        task = await self._tasks.get_task(user_id, task_id)
        if task is None:
            return None
        return TaskEvents(
            task=task,
            events=await self._events.list_events(user_id, task.project_id, task_id),
        )

    async def events_after(
        self, user_id: str, project_id: str, task_id: str, after_seq: int
    ) -> list[TaskEventRow]:
        """Timeline tail strictly newer than ``after_seq`` (the SSE cursor)."""
        return await self._events.list_events_after(user_id, project_id, task_id, after_seq)

    # -- lists ---------------------------------------------------------

    async def list_for_project(self, user_id: str, project_id: str) -> list[TaskRow]:
        return await self._tasks.list_tasks(user_id, project_id)

    async def list_all(self, user_id: str, *, limit: int = 50) -> list[TaskRow]:
        """Cross-project list, newest activity first (sidebar TASKS section)."""
        return await self._tasks.list_all(user_id, limit=limit)

    async def titles_by_ids(self, user_id: str, task_ids: list[str]) -> dict[str, str]:
        """id → title, for labelling trigger provenance without N+1 lookups."""
        return await self._tasks.get_titles_by_ids(user_id, task_ids)

    # -- intervention writes -------------------------------------------

    async def add_note(self, user_id: str, task: TaskRow, text: str) -> None:
        """Append a user note. Does not interrupt the lead."""
        await self._events.append_event(
            user_id,
            task.project_id,
            task.id,
            "user_note",
            actor="user",
            payload={"text": text},
        )

    async def revise_goal(self, user_id: str, task: TaskRow, goal: str) -> bool:
        """Update ``task.goal`` AND push the revision to a running lead.

        Both halves matter: the goal is baked into the lead session at spawn as
        its brief and its goal-mode loop condition, so a bare row update is
        pull-only — a running lead never re-reads it and would keep working
        toward the old goal. Delivery is best-effort (an offline or finished
        lead simply isn't woken; the row is updated either way) and the
        outcome is recorded on the ``goal_revised`` event so the timeline shows
        whether the lead actually heard it.

        Returns whether the running lead was notified.
        """
        from valuz_agent.modules.tasks import messaging

        task.goal = goal
        await self._tasks.update_task(task)
        notified = await messaging.notify_lead_goal_revised(
            task_id=task.id, project_id=task.project_id, new_goal=goal, user_id=user_id
        )
        delivered = bool(notified["delivered"])
        await self._events.append_event(
            user_id,
            task.project_id,
            task.id,
            "goal_revised",
            actor="user",
            payload={"goal": goal, "delivered_to_lead": delivered},
        )
        return delivered


__all__ = ["TaskDetail", "TaskEvents", "TaskService"]
