"""TaskOrchestrator — the task subsystem's single composed facade.

Holds NO implementation of its own: the composition root wires the shared
collaborators (LiveMemberRegistry, ActorRunner, event bus) into the four
services and every public method is a thin delegator. Routes and tool
handlers depend on this one name (``task_orchestrator``) — the module's
public contract. (Were the deferred kernel migration ever revived, this
method surface drafts the ``KernelClient.task_*`` wire surface.)

  Lifecycle  (``tasks/lifecycle.py``)     kickoff · draft/commit/abandon ·
                                          finish · update_deliverable ·
                                          auto-finalize + actor finalize
  Dispatch   (``tasks/dispatcher.py``)    dispatch_async (member spawn)
  Coordination (``tasks/coordination.py``) await_members · heartbeat ·
                                          shutdown broadcast
  Recovery   (``tasks/recovery.py``)      startup recovery · stop/resume ·
                                          stop_member

Related seams, deliberately NOT here:
  - host-knowledge session resolution → ``tasks/resolution.py``
    (the future MemberResolverPort host implementation, design §5.1)
  - composed terminal writes → ``tasks/events.finalize_task``
  - tool gate policy → ``tasks/tools/gate.py`` (pure; moves with the D5
    kernel-served tool surface); wire enforcement in ``tools/handlers.py``
  - read-side queries → ``tasks/queries.py`` · plan authoring/review →
    ``tasks/planning.py`` · messaging/inject → ``tasks/messaging.py``
"""

# ruff: noqa: I001
from __future__ import annotations

import logging
from typing import Any, Literal

import valuz_agent.boot.kernel  # noqa: F401

from valuz_agent.infra.eventbus import EventBus, event_bus as _global_bus
from valuz_agent.modules.tasks.resolution import (  # noqa: F401 — re-exported
    _credential_gap,
    _provider_resolver_deps,
)
from valuz_agent.modules.tasks.actor_runner import (
    ActorRunner,
    collect_manifest,
    run_session_to_idle,
    _member_run_dir,  # noqa: F401 — re-exported for tests + back-compat
)
from valuz_agent.modules.tasks.coordination import CoordinationService
from valuz_agent.modules.tasks.dispatcher import DispatcherService
from valuz_agent.modules.tasks.lifecycle import LifecycleService
from valuz_agent.modules.tasks.live_member_registry import LiveMemberRegistry
from valuz_agent.modules.tasks.models import TaskRow
from valuz_agent.modules.tasks.recovery import RecoveryService


logger = logging.getLogger(__name__)


def _require_user_id(user_id: str | None) -> str:
    if user_id is None:
        raise ValueError("user_id is required")
    return user_id


# ``run_session_to_idle`` / ``collect_manifest`` / ``_member_run_dir`` and the
# actor-loop tuning constants now live in the runtime layer
# (``tasks/actor_runner.py``, ADR-023). They are imported above and re-exported
# from this module so existing call sites + tests keep importing them here.

# ``await_member_results`` / ``_heartbeat_pending`` / the member-idle notify +
# lead-idle-no-pending callbacks / ``_broadcast_shutdown`` and the lead↔member
# text delivery (send_to_member / inject_into_task / notify_lead_goal_revised)
# now live in :class:`CoordinationService` (``tasks/coordination.py``, ADR-023
# Step 3b). The orchestrator keeps thin delegators so its public coordination
# surface + the actor-loop role callbacks keep resolving on ``self``.

# ``_credential_gap`` / ``_provider_resolver_deps`` now live in the session
# resolver (``tasks/resolution.py``). They are imported above and re-exported
# here so existing call sites + tests keep importing them from this module.


# ---------------------------------------------------------------------------
# TaskOrchestrator
# ---------------------------------------------------------------------------


class TaskOrchestrator:
    """Drives the full task lifecycle — kickoff, dispatch, finish.

    Instantiated once at startup (like schedule_runner); registered in
    app.py and passed to register_dispatch_tools().
    """

    def __init__(
        self,
        bus: EventBus | None = None,
        registry: LiveMemberRegistry | None = None,
        actor_runner: ActorRunner | None = None,
    ) -> None:
        self._bus = bus or _global_bus
        # Live member tracking: task_id → live member session ids (so
        # finish_task can broadcast shutdown to every still-running member) and
        # session_id → dispatch-start epoch (manifest attribution under the
        # shared project cwd). See LiveMemberRegistry for the sync invariant.
        self._members = registry or LiveMemberRegistry()
        # The shared runtime turn/actor engine (ADR-023). Bind ``self`` as the
        # host so the loop's seams (_run_turn_with_sink / _finalize_actor /
        # _notify_lead_member_idle / _lead_idle_with_no_pending) resolve back to
        # this orchestrator at call time — preserving the existing behaviour
        # where the loop drives those methods (and lets tests stub them).
        self._actor = actor_runner or ActorRunner()
        self._actor.bind(self)
        # Subtask dispatch (async member spawn) lives in DispatcherService
        # (ADR-023 Step 3a). It shares this orchestrator's registry + runtime
        # ActorRunner (same instances); the orchestrator's public
        # dispatch_async delegates straight onto it.
        self._dispatcher = DispatcherService(
            registry=self._members,
            actor_runner=self._actor,
        )
        # Lead ↔ member coordination (await_members / heartbeat / shutdown
        # broadcast / member-idle notify / text delivery) lives in
        # CoordinationService (ADR-023 Step 3b). It shares this orchestrator's
        # registry (same instance) for has_live_members / dispatch_started_at /
        # drain_members; the orchestrator's coordination surface delegates
        # straight onto it, and the ActorRunner resolves its role callbacks
        # (_notify_lead_member_idle / _lead_idle_with_no_pending) through the
        # bound host onto this service.
        self._coordination = CoordinationService(registry=self._members)
        # Task lifecycle (kickoff / draft / commit / abandon / finish + the
        # actor-loop finalize callbacks + the lead-clone builder) lives in
        # LifecycleService (ADR-023 Step 3c). It shares this orchestrator's
        # registry (same instance) + runtime ActorRunner + CoordinationService
        # + event bus; the orchestrator's lifecycle surface delegates straight
        # onto it, and the ActorRunner resolves its finalize callback
        # (_finalize_actor) through the bound host onto this service.
        self._lifecycle = LifecycleService(
            registry=self._members,
            actor_runner=self._actor,
            coordination=self._coordination,
            bus=self._bus,
        )
        # Startup recovery + user-initiated stop/resume lives in RecoveryService
        # (ADR-023 Step 3d). It shares this orchestrator's registry (same
        # instance — re-populated WITHOUT a dispatch epoch on the recovery
        # branch, the Step-1 invariant) + runtime ActorRunner + CoordinationService;
        # the orchestrator's recovery surface delegates straight onto it.
        self._recovery = RecoveryService(
            registry=self._members,
            actor_runner=self._actor,
            coordination=self._coordination,
        )

    # ------------------------------------------------------------------
    # kickoff
    # ------------------------------------------------------------------

    async def kickoff(
        self,
        project_id: str,
        goal: str,
        lead_agent_slug: str,
        refs: list[str] | None = None,
        created_by: str = "user",
        title: str | None = None,
        originating_session_id: str | None = None,
        trigger_type: str | None = None,
        trigger_automation_id: str | None = None,
        worktree: bool = False,
        user_id: str | None = None,
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

        Thin delegator onto :class:`LifecycleService` (ADR-023 Step 3c).
        Kept on the orchestrator so its existing callers (REST routes,
        in-process automation runner, the ``create_task`` MCP handler) keep
        invoking ``task_orchestrator.kickoff``.
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

    # ------------------------------------------------------------------
    # Chat-plan-then-execute (VALUZ-CHATPLAN) — Slice 2
    # ------------------------------------------------------------------

    async def draft_task(
        self,
        *,
        project_id: str,
        goal: str,
        lead_agent_slug: str,
        originating_session_id: str,
        refs: list[str] | None = None,
        title: str | None = None,
        user_id: str | None = None,
    ) -> TaskRow:
        """Create a ``draft`` task without a lead session (VALUZ-CHATPLAN).

        Thin delegator onto :class:`LifecycleService` (ADR-023 Step 3c).
        """
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
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Flip a draft task to ``active`` by spawning its lead session.

        Thin delegator onto :class:`LifecycleService` (ADR-023 Step 3c).
        """
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
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Discard a draft task (status: draft → abandoned).

        Thin delegator onto :class:`LifecycleService` (ADR-023 Step 3c).
        """
        return await self._lifecycle.abandon_task(
            task_id=task_id,
            project_id=project_id,
            caller_session_id=caller_session_id,
            reason=reason,
            user_id=user_id,
        )

    # ==================================================================
    # v2 actor dispatch (M10 附录 B) — persistent lead + member actors
    # ==================================================================

    async def _run_turn_with_sink(
        self, session_id: str, content: str, user_id: str | None = None
    ) -> str:
        """Run ONE turn on a persistent session and return its final status.

        Thin delegator onto the shared :class:`ActorRunner` runtime engine
        (ADR-023). Kept as a method so the actor loop drives it via ``self``
        (and tests can stub ``orch._run_turn_with_sink``).
        """
        return await self._actor._run_turn_with_sink(session_id, content, user_id=user_id)

    async def run_actor_loop(
        self,
        *,
        session_id: str,
        initial_prompt: str,
        role: Literal["lead", "subtask"],
        task_id: str,
        project_id: str,
        idle_ttl: float | None = None,
        user_id: str | None = None,
    ) -> None:
        """Persistent actor loop: run turn → idle → await mailbox → repeat.

        Delegates to the shared :class:`ActorRunner`; the runner resolves the
        loop's seams (_run_turn_with_sink / _finalize_actor /
        _notify_lead_member_idle / _lead_idle_with_no_pending) back through this
        orchestrator (bound as its host), preserving the prior behaviour.
        """
        await self._actor.run_actor_loop(
            session_id=session_id,
            initial_prompt=initial_prompt,
            role=role,
            task_id=task_id,
            project_id=project_id,
            idle_ttl=idle_ttl,
            user_id=user_id,
        )

    @staticmethod
    def _format_member_done(msg: Any) -> str:
        """Render a member_done mailbox message as the lead's next turn prompt."""
        return ActorRunner._format_member_done(msg)

    async def _notify_lead_member_idle(
        self, session_id: str, status: str, user_id: str | None = None
    ) -> None:
        """After a member turn, push a member_done message to its lead's inbox.

        Thin delegator onto :class:`CoordinationService` (ADR-023 Step 3b).
        Kept as a method so the actor loop drives it via the bound host (and
        tests can stub ``orch._notify_lead_member_idle``).
        """
        await self._coordination._notify_lead_member_idle(session_id, status, user_id=user_id)

    async def _lead_idle_with_no_pending(
        self, task_id: str, project_id: str, user_id: str | None = None
    ) -> bool:
        """True when a lead has nothing left to wait for after a turn.

        Thin delegator onto :class:`CoordinationService` (ADR-023 Step 3b).
        Kept as a method so the actor loop drives it via the bound host (and
        tests can stub ``orch._lead_idle_with_no_pending``).
        """
        return await self._coordination._lead_idle_with_no_pending(
            task_id, project_id, user_id=user_id
        )

    async def _auto_finalize_lead_task(
        self,
        *,
        lead_session_id: str,
        task_id: str,
        project_id: str,
        final_status: str,
        user_id: str | None = None,
    ) -> None:
        """Host-side terminal fallback when a lead loop ends without finish_task
        (ADR-023 Step 3c). Thin delegator onto :class:`LifecycleService` (kept as
        a method so the loop seam + tests can drive ``orch._auto_finalize_lead_task``)."""
        await self._lifecycle._auto_finalize_lead_task(
            lead_session_id=lead_session_id,
            task_id=task_id,
            project_id=project_id,
            final_status=final_status,
            user_id=user_id,
        )

    # ------------------------------------------------------------------
    # Stop / resume (VALUZ-RESUME)
    # ------------------------------------------------------------------

    async def recover_active_tasks(self) -> int:
        """Startup Layer-1 recovery sweep — delegates to :class:`RecoveryService`."""
        return await self._recovery.recover_active_tasks()

    async def _recover_one_task(
        self, task_id: str, project_id: str, user_id: str | None = None
    ) -> bool:
        return await self._recovery._recover_one_task(task_id, project_id, user_id=user_id)

    async def _interrupt_kernel_session(self, session_id: str, user_id: str | None = None) -> None:
        await self._recovery._interrupt_kernel_session(session_id, user_id=user_id)

    async def stop_task(
        self,
        task_id: str,
        project_id: str,
        *,
        target_status: str = "paused",
        user_id: str | None = None,
    ) -> bool:
        return await self._recovery.stop_task(
            task_id, project_id, target_status=target_status, user_id=user_id
        )

    async def resume_task(
        self,
        task_id: str,
        project_id: str,
        *,
        actor: str = "user",
        user_id: str | None = None,
        instruction: str | None = None,
    ) -> dict[str, Any]:
        return await self._recovery.resume_task(
            task_id, project_id, actor=actor, user_id=user_id, instruction=instruction
        )

    async def stop_member(self, session_id: str, user_id: str | None = None) -> bool:
        return await self._recovery.stop_member(session_id, user_id=user_id)

    async def _finalize_actor(
        self,
        *,
        session_id: str,
        last_content: str,
        final_status: str,
        role: Literal["lead", "subtask"],
        task_id: str,
        project_id: str,
        via_shutdown: bool = False,
        user_id: str | None = None,
    ) -> None:
        """Finalize a session once its actor loop ends (ADR-023 Step 3c).

        Thin delegator onto :class:`LifecycleService`, which owns the single
        implementation. Kept as a method so the ActorRunner can drive it via the
        bound host (``run_actor_loop``'s ``finally`` resolves ``_finalize_actor``
        onto this orchestrator).
        """
        await self._lifecycle._finalize_actor(
            session_id=session_id,
            last_content=last_content,
            final_status=final_status,
            role=role,
            task_id=task_id,
            project_id=project_id,
            via_shutdown=via_shutdown,
            user_id=user_id,
        )

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
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Start a planned subtask's member actor (non-blocking); return its handle.

        Plan-first (VALUZ-TASK): the subtask must be dispatchable in the plan;
        agent/goal default to the plan node. Unlike :meth:`dispatch`, this
        records the run, starts the member's actor loop as a sibling task, and
        returns ``{session_id, agent, status:"dispatched"}`` immediately. The
        lead is re-woken via ``member_done``; the node goes ``in_review`` then
        and is completed only by ``review_subtask``.
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

    # send_to_member / inject_into_task implementations live in
    # tasks/messaging.py (T1.1 split) — callers invoke messaging.* directly.

    # ------------------------------------------------------------------
    # await_members (v0.14) — turn-内阻塞收集并行 member 结果
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
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Block (inside the lead's turn) until dispatched members finish.

        Thin delegator onto :class:`CoordinationService` (ADR-023 Step 3b).
        Kept as a method so the dispatch-MCP ``await_members`` handler keeps
        calling it on the orchestrator (and tests can drive
        ``orch.await_member_results``).
        """
        return await self._coordination.await_member_results(
            lead_session_id=lead_session_id,
            project_id=project_id,
            task_id=task_id,
            keys=keys,
            mode=mode,
            timeout_s=timeout_s,
            user_id=user_id,
        )

    async def _heartbeat_pending(
        self,
        *,
        task_id: str,
        project_id: str,
        pending_keys: set[str],
        user_id: str | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Backstop for bad-case #3 (VALUZ-RESUME §5.4): a member whose kernel
        session went terminal but whose ``member_done`` never reached the lead's
        mailbox (delivery window / crash before finalize).

        Thin delegator onto :class:`CoordinationService` (ADR-023 Step 3b).
        Kept as a method so existing tests can drive ``orch._heartbeat_pending``.
        """
        return await self._coordination._heartbeat_pending(
            task_id=task_id,
            project_id=project_id,
            pending_keys=pending_keys,
            user_id=user_id,
        )

    def _broadcast_shutdown(self, task_id: str) -> None:
        """Tell every still-running member of a task to finalize after its turn.

        Thin delegator onto :class:`CoordinationService` (ADR-023 Step 3b).
        Kept as a method so ``finish_task`` / ``stop_task`` drive it on the
        orchestrator (and tests can call ``orch._broadcast_shutdown``).
        """
        self._coordination._broadcast_shutdown(task_id)

    # ------------------------------------------------------------------
    # finish_task
    # ------------------------------------------------------------------

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
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Close the task — append the terminal event and set the task status.

        Thin delegator onto :class:`LifecycleService` (ADR-023 Step 3c).
        """
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
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Refresh the deliverable card on a completed task (follow-up chat).

        Thin delegator onto :class:`LifecycleService` (ADR-023 Step 3c).
        """
        return await self._lifecycle.update_deliverable(
            task_id=task_id,
            project_id=project_id,
            lead_session_id=lead_session_id,
            summary=summary,
            artifacts=artifacts,
            user_id=user_id,
        )

    # ------------------------------------------------------------------
    # Plan / review — lead orchestration (VALUZ-TASK)
    # ------------------------------------------------------------------

    # Plan authoring / review / node mutation live in tasks/planning.py
    # (T1.1 split). Callers (dispatch-MCP tools, task routes) invoke
    # planning.* directly; the orchestrator's own dispatch/actor/recovery
    # methods do the same.

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _materialize_lead_agent(self, base_agent: Any) -> Any:
        """Materialize a per-task lead clone of *base_agent*.

        Thin delegator onto :class:`LifecycleService` (ADR-023 Step 3c) —
        kept as a method so tests keep driving ``orch._materialize_lead_agent``.
        """
        return await self._lifecycle._materialize_lead_agent(base_agent)


# ---------------------------------------------------------------------------
# Module-level singleton (used by app.py startup + dispatch_mcp handlers)
# ---------------------------------------------------------------------------

task_orchestrator = TaskOrchestrator()

__all__ = [
    "TaskOrchestrator",
    "task_orchestrator",
    "run_session_to_idle",
    "collect_manifest",
    "_member_run_dir",
    "_credential_gap",
    "_provider_resolver_deps",
]
