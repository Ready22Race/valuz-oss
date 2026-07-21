"""DispatcherService — subtask dispatch (ADR-023, Step 3a).

Owns the async member-spawn path (``dispatch_async`` — the only dispatch
surface since the tool collapse, decision doc §14; the legacy sync
``dispatch`` / ``dispatch_batch`` paths are deleted). Owns no task state — it
receives the shared :class:`LiveMemberRegistry` and the runtime
:class:`ActorRunner` by constructor injection (the same instances the
composition root wires into every other task service).

Collaboration with sibling MODULES (planning, the build-session helpers) stays
direct-import; only the registry + runner are injected.

CRITICAL invariant (``dispatch_async``): the sync-before-spawn block must run
``mailbox_registry.register(lead) -> registry.add_member(...) ->
mailbox_registry.register(member) -> asyncio.create_task(run_actor_loop)`` with
NO ``await`` in between — a racing ``finish_task`` shutdown broadcast that sees
an empty live set would otherwise drop the just-spawned member.
"""

# ruff: noqa: I001
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Literal, cast

from valuz_agent.adapters import kernel_client
from valuz_agent.ports.sandbox_allocator import SandboxScope
from valuz_agent.modules.sessions import project_index
from valuz_agent.adapters.agent_resolver import (
    build_member_session,
    resolve_agent_display_name,
    spill_goal_brief_if_too_long,
)
from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.infra.fs_registry import fs_registry
from valuz_agent.modules.agents.datastore import ProjectMemberDatastore
from valuz_agent.modules.tasks import planning
from valuz_agent.modules.tasks._session_build import (
    _credential_gap,
    _provider_resolver_deps,
)
from valuz_agent.modules.tasks.actor_runner import (
    ActorRunner,
    _member_run_dir,
)
from valuz_agent.modules.tasks.datastore import (
    TaskDatastore,
    TaskEventDatastore,
    TaskSessionDatastore,
)
from valuz_agent.modules.tasks.live_member_registry import LiveMemberRegistry
from valuz_agent.modules.tasks.models import TaskSessionRow
from valuz_agent.modules.tasks.plan import TaskPlan
from valuz_agent.modules.tasks.task_worktree import (
    resolve_task_cwd,
    task_worktree_notice,
    task_worktree_snapshot,
)

logger = logging.getLogger(__name__)


class DispatcherService:
    """Drives async subtask dispatch (member actor spawn).

    Constructed once at the composition root with the shared registry and the
    runtime ActorRunner (the same instances every other task service shares).
    """

    def __init__(
        self,
        *,
        registry: LiveMemberRegistry,
        actor_runner: ActorRunner,
    ) -> None:
        self._members = registry
        self._actor = actor_runner

    # ==================================================================
    # v2 actor dispatch (M10 附录 B) — async member spawn
    # ==================================================================

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
        async with async_unit_of_work() as db:
            task_ds = TaskDatastore(db)
            event_ds = TaskEventDatastore(db)
            run_ds = TaskSessionDatastore(db)
            member_ds = ProjectMemberDatastore(db)

            task_row = await task_ds.get_task_by_project(
                user_id, project_id, task_id
            )
            if task_row is None:
                return {"error": f"task {task_id!r} not found", "status": "failed"}
            _plan = TaskPlan.from_dict(task_row.plan)
            resolved = planning.resolve_dispatch_node(_plan, subtask_key, agent, goal)
            if isinstance(resolved, str):
                return {"error": resolved, "status": "failed"}
            agent, goal = resolved
            _node = _plan.get(subtask_key)
            review_criteria = _node.review_criteria if _node else ""

            from valuz_agent.modules.projects.datastore import ProjectDatastore

            ws_ds = ProjectDatastore(db)
            ws_row = await ws_ds.get_by_id(task_row.user_id, project_id)
            if ws_row is None:
                return {"error": f"project {project_id!r} not found", "status": "failed"}
            project_cwd = fs_registry.project_cwd(
                task_row.user_id,
                ws_row.id,
                cast(
                    Literal["chat", "project"],
                    ws_row.kind if ws_row.kind in ("chat", "project") else "chat",
                ),
                ws_row.root_path,
            )

            run_seq = await run_ds.next_sequence(task_id)
            # Task-level worktree (design §5): members share the task's ONE
            # worktree cwd — no per-member isolation on top (see the sync
            # dispatch variant for the full rationale).
            wt_snapshot = task_worktree_snapshot(task_row)
            if wt_snapshot is not None:
                mode = "shared"
                work_cwd = await resolve_task_cwd(task_row, str(project_cwd))
            else:
                mode = project_mode or "shared"
                work_cwd = str(project_cwd)
            # Legacy per-member ``repo-worktree`` shells out to ``git worktree
            # add`` (blocking subprocess); offload so dispatch never blocks the
            # event loop. The default ``shared`` mode is a no-op Path().
            run_dir = await asyncio.to_thread(_member_run_dir, work_cwd, task_id, run_seq, mode)

            refs_text = "\n".join(f"- {r}" for r in (refs or []))
            # Goal mode prepends ``/goal `` (wrap_for_mode); drop the redundant
            # ``## Goal`` header so it doesn't land inside the goal condition.
            # Append the lead's review criteria so the member knows the
            # acceptance bar it will be reviewed against.
            member_brief = (
                goal
                + (f"\n\n## References\n\n{refs_text}" if refs_text else "")
                + (
                    "\n\n## Acceptance criteria (you will be reviewed on this)\n\n"
                    + review_criteria
                    if review_criteria
                    else ""
                )
            )
            # Fence the goal-mode payload: spill an over-cap member brief to a
            # doc and pass a short pointer (used as both the embedded brief and
            # the member's initial ``/goal`` prompt).
            # Spill anchored at the MAIN project cwd: a brief doc written
            # into the (work)tree would register as work worth keeping and
            # defeat the task worktree's clean-teardown (design R5).
            member_brief = spill_goal_brief_if_too_long(
                member_brief,
                run_dir=str(project_cwd),
                task_id=task_id,
                label=f"{agent}-{subtask_key}",
                is_lead=False,
            )

            ws_ctx = await ws_ds.get_context(task_row.user_id, project_id)
            project_instructions_md = ws_ctx.instructions_md if ws_ctx else None

            member_session = await build_member_session(
                project_id=project_id,
                agent_slug=agent,
                members=member_ds,
                is_lead=False,
                task_id=task_id,
                run_dir=str(run_dir),
                brief=member_brief,
                project_name=ws_row.name,
                project_instructions_md=project_instructions_md,
                lead_session_id=lead_session_id,
                # Member sub-run in goal mode: kernel wraps the brief into
                # ``/goal <brief>`` so the member self-loops until its scoped
                # goal is met, then auto-exits and reports member_done.
                # (The collapsed ``dispatch`` tool routes here, so this is the
                # live path — the now-unused sync ``dispatch`` had it too.)
                goal_mode=True,
                worktree_notice=task_worktree_notice(wt_snapshot),
                user_id=task_row.user_id,
                **_provider_resolver_deps(db),
            )
            if member_session is None:
                return {
                    "error": f"agent {agent!r} not found in project {project_id!r}",
                    "status": "failed",
                }

            # Durable member display name for the event payloads below — see the
            # note in ``dispatch`` (frontend reads ``agent_name`` instead of
            # joining the ``actor`` slug against an async members list).
            agent_name = await resolve_agent_display_name(project_id, agent, user_id)

            # Fail fast on a credential-less member before starting its actor.
            gap = await _credential_gap(member_session, agent, db=db, user_id=user_id)
            if gap is not None:
                await event_ds.append_event(
                    user_id,
                    project_id=project_id,
                    task_id=task_id,
                    type="subtask_failed",
                    actor=agent,
                    session_id=member_session.id,
                    payload={
                        "agent": agent,
                        "agent_name": agent_name,
                        "status": "failed",
                        "error": gap,
                    },
                )
                return {"error": gap, "status": "failed", "agent": agent}

            await kernel_client.create_session(
                user_id, member_session, scope=SandboxScope(kind="task", id=task_id)
            )
            await project_index.record(
                project_id,
                member_session.id,
                kind="task_subtask",
                origin="task",
                user_id=user_id,
            )

            await run_ds.create_run(
                user_id,
                TaskSessionRow(
                    project_id=project_id,
                    task_id=task_id,
                    session_id=member_session.id,
                    agent_slug=agent,
                    sequence=run_seq,
                    kind="subtask",
                    status="active",
                    goal=goal,
                    dispatched_by=lead_session_id,
                    project_mode=mode,
                    run_dir=str(run_dir),
                    subtask_key=subtask_key,
                ),
            )
            await event_ds.append_event(
                user_id,
                project_id=project_id,
                task_id=task_id,
                type="subtask_spawned",
                actor=agent,
                session_id=member_session.id,
                payload={
                    "agent": agent,
                    "agent_name": agent_name,
                    "goal": goal,
                    "run_dir": str(run_dir),
                    "subtask_key": subtask_key,
                },
            )

        # Flip the plan node to in_progress (attempts++, link this run).
        await planning.mark_node_dispatched(
            project_id=project_id,
            task_id=task_id,
            subtask_key=subtask_key,
            agent=agent,
            session_id=member_session.id,
            user_id=user_id,
        )

        # Track as a live member + start its actor loop (non-blocking).
        # Register the mailbox SYNCHRONOUSLY (before create_task) so a
        # finish_task shutdown that races ahead of the member loop's first tick
        # is still queued rather than dropped — otherwise the member would hang
        # until its idle TTL. run_actor_loop's register() is idempotent.
        from valuz_agent.modules.tasks.mailbox import mailbox_registry

        # Register the LEAD's mailbox too (idempotent) — the member posts
        # ``member_done`` here when it idles, and the lead's ``await_members``
        # drains it. Registering at dispatch time guarantees delivery even
        # when the lead wasn't started via the async-kickoff path (e.g. a
        # goal-mode single-turn lead): otherwise the member's ``put`` lands on
        # an unregistered inbox and is DROPPED, and ``await_members`` raises
        # KeyError + returns empty → the lead wrongly thinks members are stuck.
        mailbox_registry.register(lead_session_id)
        self._members.add_member(task_id, member_session.id, dispatch_epoch=time.time())
        mailbox_registry.register(member_session.id)
        asyncio.create_task(
            self._actor.run_actor_loop(
                session_id=member_session.id,
                initial_prompt=member_brief,
                role="subtask",
                task_id=task_id,
                project_id=project_id,
                user_id=user_id,
            )
        )

        return {
            "session_id": member_session.id,
            "agent": agent,
            "status": "dispatched",
        }
