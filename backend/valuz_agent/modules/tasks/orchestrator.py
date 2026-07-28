"""TaskOrchestrator — the task subsystem's composition root + public facade.

Two jobs, and nothing else:

1. **Compose.** Build the shared collaborators (``LiveMemberRegistry``,
   ``ActorRunner``) once and wire them into the four services, so every service
   sees the *same* registry and the *same* runner. The ordering is load-bearing
   — see :meth:`__init__`.
2. **Expose the public surface.** Routes, the task MCP tool handlers, the
   automation runner and boot all depend on the single name
   ``task_orchestrator``; the twelve methods below are that contract.

  Lifecycle    (``tasks/lifecycle.py``)    kickoff · draft/commit/abandon ·
                                           finish_task · update_deliverable
  Dispatch     (``tasks/dispatcher.py``)   dispatch_async (member spawn)
  Coordination (``tasks/coordination.py``) await_member_results
  Recovery     (``tasks/recovery.py``)     recover_active_tasks · stop_task ·
                                           resume_task · stop_member

Anything NOT in that list is service-internal. The facade used to carry a
further dozen delegators (``_finalize_actor`` / ``_run_turn_with_sink`` /
``_heartbeat_pending`` / ``_recover_one_task`` / …) that no production caller
ever used — they existed so tests could reach through the facade, and they are
exactly what kept the actor seam untyped. Tests now drive the owning service,
reachable via the read-only properties below (:attr:`lifecycle`,
:attr:`coordination`, :attr:`recovery`, :attr:`dispatcher`, :attr:`actor`).

Related seams, deliberately NOT here:
  - host-knowledge session resolution → ``tasks/resolution.py``
    (the future MemberResolverPort host implementation, design §5.1)
  - composed terminal writes → ``tasks/events.finalize_task``
  - tool gate policy → ``tasks/tools/gate.py`` (pure; moves with the D5
    kernel-served tool surface); wire enforcement in ``tools/handlers.py``
  - read-side queries → ``tasks/queries.py`` · plan authoring/review →
    ``tasks/planning.py`` · mailbox delivery → ``tasks/messaging.py``
"""

# ruff: noqa: I001
from __future__ import annotations

import logging
from typing import Any

import valuz_agent.boot.kernel  # noqa: F401

from valuz_agent.modules.tasks.actor_runner import ActorRunner
from valuz_agent.modules.tasks.coordination import CoordinationService
from valuz_agent.modules.tasks.dispatcher import DispatcherService
from valuz_agent.modules.tasks.lifecycle import LifecycleService
from valuz_agent.modules.tasks.live_member_registry import LiveMemberRegistry
from valuz_agent.modules.tasks.models import TaskRow
from valuz_agent.modules.tasks.recovery import RecoveryService


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TaskOrchestrator
# ---------------------------------------------------------------------------


class TaskOrchestrator:
    """Drives the full task lifecycle — kickoff, dispatch, finish.

    Instantiated once at startup (like schedule_runner); the boot steps pass it
    to ``tools.handlers.build_task_tool_defs``, whose closures capture it.
    """

    def __init__(
        self,
        registry: LiveMemberRegistry | None = None,
        actor_runner: ActorRunner | None = None,
    ) -> None:
        # Shared live-member tracking: task_id → live member session ids (so
        # finish_task can broadcast shutdown to every still-running member) and
        # session_id → dispatch-start epoch (manifest attribution under the
        # shared project cwd). Every service below gets THIS instance — see
        # LiveMemberRegistry for the no-await-between-spawn-and-register
        # invariant that makes sharing load-bearing.
        self._members = registry or LiveMemberRegistry()

        # Wiring order is forced by a cycle: the services need the runner as a
        # constructor argument, and the runner needs two of them back. So build
        # the runner first, then the services, then bind (below).
        self._actor = actor_runner or ActorRunner()
        self._dispatcher = DispatcherService(
            registry=self._members,
            actor_runner=self._actor,
        )
        self._coordination = CoordinationService(registry=self._members)
        self._lifecycle = LifecycleService(
            registry=self._members,
            actor_runner=self._actor,
            coordination=self._coordination,
        )
        self._recovery = RecoveryService(
            registry=self._members,
            actor_runner=self._actor,
            coordination=self._coordination,
        )

        # Close the cycle. The actor loop runs its own turns and delegates
        # everything around a turn to these two — typed as ActorFinalizer /
        # ActorCoordinator, so mypy checks that the services still satisfy the
        # seam (an untyped handle here previously let delegators rot silently).
        self._actor.bind(finalizer=self._lifecycle, coordinator=self._coordination)

    # ------------------------------------------------------------------
    # Composed parts — read-only access for tests + diagnostics.
    #
    # This is the composition root, so handing out the wired services is
    # legitimate and is what lets the public surface stay at twelve methods:
    # a test that wants ``finalize_actor`` asks ``orch.lifecycle`` for it
    # instead of forcing a pass-through onto the facade.
    # ------------------------------------------------------------------

    @property
    def members(self) -> LiveMemberRegistry:
        """The shared live-member registry every service writes through."""
        return self._members

    @property
    def actor(self) -> ActorRunner:
        """The shared turn/actor-loop engine."""
        return self._actor

    @property
    def lifecycle(self) -> LifecycleService:
        """kickoff / draft / commit / abandon / finish + actor finalize."""
        return self._lifecycle

    @property
    def dispatcher(self) -> DispatcherService:
        """Async subtask dispatch (member actor spawn)."""
        return self._dispatcher

    @property
    def coordination(self) -> CoordinationService:
        """Lead ↔ member coordination (await / heartbeat / shutdown broadcast)."""
        return self._coordination

    @property
    def recovery(self) -> RecoveryService:
        """Startup recovery + user-initiated stop / resume."""
        return self._recovery

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def kickoff(
        self,
        project_id: str,
        goal: str,
        lead_agent_slug: str,
        *,
        refs: list[str] | None = None,
        created_by: str = "user",
        title: str | None = None,
        originating_session_id: str | None = None,
        trigger_type: str | None = None,
        trigger_automation_id: str | None = None,
        worktree: bool = False,
        user_id: str,
    ) -> TaskRow:
        """Create a task and start its lead session in the background.

        The lead runs as a persistent actor re-woken by ``member_done`` /
        ``send`` until ``finish_task``. Returns the newly created TaskRow.

        An over-long ``goal`` is spilled to a doc and the lead receives a short
        pointer to read (see ``spill_goal_brief_if_too_long``) rather than
        crashing the ``/goal`` payload mid-turn.

        ``user_id`` is the owning caller; it MUST be threaded through to the
        project / member lookups (owner-scoped queries return None for the wrong
        owner — a missing ``user_id`` reads as "project not found").
        """
        return await self._lifecycle.kickoff(
            project_id=project_id,
            goal=goal,
            lead_agent_slug=lead_agent_slug,
            refs=refs,
            created_by=created_by,
            title=title,
            originating_session_id=originating_session_id,
            trigger_type=trigger_type,
            trigger_automation_id=trigger_automation_id,
            worktree=worktree,
            user_id=user_id,
        )

    async def draft_task(
        self,
        *,
        project_id: str,
        goal: str,
        lead_agent_slug: str,
        originating_session_id: str,
        refs: list[str] | None = None,
        title: str | None = None,
        user_id: str,
    ) -> TaskRow:
        """Create a ``draft`` task without a lead session (VALUZ-CHATPLAN)."""
        return await self._lifecycle.draft_task(
            project_id=project_id,
            goal=goal,
            lead_agent_slug=lead_agent_slug,
            originating_session_id=originating_session_id,
            refs=refs,
            title=title,
            user_id=user_id,
        )

    async def commit_task(
        self,
        *,
        task_id: str,
        project_id: str,
        caller_session_id: str,
        lead_agent_slug_override: str | None = None,
        user_id: str,
    ) -> dict[str, Any]:
        """Flip a draft task to ``active`` by spawning its lead session."""
        return await self._lifecycle.commit_task(
            task_id=task_id,
            project_id=project_id,
            caller_session_id=caller_session_id,
            lead_agent_slug_override=lead_agent_slug_override,
            user_id=user_id,
        )

    async def abandon_task(
        self,
        *,
        task_id: str,
        project_id: str,
        caller_session_id: str,
        reason: str = "",
        user_id: str,
    ) -> dict[str, Any]:
        """Discard a draft task (status: draft → abandoned)."""
        return await self._lifecycle.abandon_task(
            task_id=task_id,
            project_id=project_id,
            caller_session_id=caller_session_id,
            reason=reason,
            user_id=user_id,
        )

    async def finish_task(
        self,
        *,
        task_id: str,
        project_id: str,
        lead_session_id: str,
        summary: str,
        artifacts: list[str] | None = None,
        status: str = "completed",
        force: bool = False,
        user_id: str,
    ) -> dict[str, Any]:
        """Close the task — append the terminal event and set the task status."""
        return await self._lifecycle.finish_task(
            task_id=task_id,
            project_id=project_id,
            lead_session_id=lead_session_id,
            summary=summary,
            artifacts=artifacts,
            status=status,
            force=force,
            user_id=user_id,
        )

    async def update_deliverable(
        self,
        *,
        task_id: str,
        project_id: str,
        lead_session_id: str,
        summary: str,
        artifacts: list[str] | None = None,
        user_id: str,
    ) -> dict[str, Any]:
        """Refresh the deliverable card on a completed task (follow-up chat)."""
        return await self._lifecycle.update_deliverable(
            task_id=task_id,
            project_id=project_id,
            lead_session_id=lead_session_id,
            summary=summary,
            artifacts=artifacts,
            user_id=user_id,
        )

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def dispatch_async(
        self,
        *,
        task_id: str,
        project_id: str,
        lead_session_id: str,
        subtask_key: str,
        agent: str | None = None,
        goal: str | None = None,
        refs: list[str] | None = None,
        project_mode: str | None = None,
        user_id: str,
    ) -> dict[str, Any]:
        """Start a planned subtask's member actor (non-blocking); return its handle.

        Plan-first (VALUZ-TASK): the subtask must be dispatchable in the plan;
        agent/goal default to the plan node. Records the run, starts the
        member's actor loop as a sibling task, and returns
        ``{session_id, agent, status:"dispatched"}`` immediately. The lead is
        re-woken via ``member_done``; the node goes ``in_review`` then and is
        completed only by ``review_subtask``.
        """
        return await self._dispatcher.dispatch_async(
            task_id=task_id,
            project_id=project_id,
            lead_session_id=lead_session_id,
            subtask_key=subtask_key,
            agent=agent,
            goal=goal,
            refs=refs,
            project_mode=project_mode,
            user_id=user_id,
        )

    # ------------------------------------------------------------------
    # Coordination
    # ------------------------------------------------------------------

    async def await_member_results(
        self,
        *,
        lead_session_id: str,
        project_id: str,
        task_id: str,
        keys: list[str] | None = None,
        mode: str = "all",
        timeout_s: float | None = None,
        user_id: str,
    ) -> dict[str, Any]:
        """Block (inside the lead's turn) until dispatched members finish."""
        return await self._coordination.await_member_results(
            lead_session_id=lead_session_id,
            project_id=project_id,
            task_id=task_id,
            keys=keys,
            mode=mode,
            timeout_s=timeout_s,
            user_id=user_id,
        )

    # ------------------------------------------------------------------
    # Recovery / stop / resume
    # ------------------------------------------------------------------

    async def recover_active_tasks(self) -> int:
        """Startup Layer-1 recovery sweep over every owner's ``active`` tasks."""
        return await self._recovery.recover_active_tasks()

    async def stop_task(
        self,
        task_id: str,
        project_id: str,
        *,
        target_status: str = "paused",
        user_id: str,
    ) -> bool:
        """User-initiated cascade halt → ``paused`` (pause) or ``stopped`` (stop)."""
        return await self._recovery.stop_task(
            task_id, project_id, target_status=target_status, user_id=user_id
        )

    async def resume_task(
        self,
        task_id: str,
        project_id: str,
        *,
        actor: str = "user",
        user_id: str,
        instruction: str | None = None,
    ) -> dict[str, Any]:
        """Resume a ``paused`` / ``blocked`` / ``stopped`` / ``completed`` task."""
        return await self._recovery.resume_task(
            task_id, project_id, actor=actor, user_id=user_id, instruction=instruction
        )

    async def stop_member(self, session_id: str, user_id: str) -> bool:
        """User-initiated single-member stop (the task stays ``active``)."""
        return await self._recovery.stop_member(session_id, user_id=user_id)


# ---------------------------------------------------------------------------
# Module-level singleton (used by app.py startup + the tool handlers)
# ---------------------------------------------------------------------------

task_orchestrator = TaskOrchestrator()

__all__ = ["TaskOrchestrator", "task_orchestrator"]
