"""RecoveryService — startup recovery + user-initiated stop / resume.

The durable truth for a member's liveness is its kernel session state + the
host run/plan rows, never the in-memory mailbox (which dies with the process).
The pure state→disposition rules live in ``member_state``; this service
applies their side effects: Layer-1 startup sweep (``recover_active_tasks``),
Layer-2 ``stop_task`` / ``resume_task`` / ``stop_member``, and the shared
``_recover_one_task`` reconcile-and-respawn machine.
"""

# ruff: noqa: I001
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from valuz_agent.adapters import kernel_client
from valuz_agent.adapters.agent_resolver import spill_goal_brief_if_too_long
from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.tasks import planning
from valuz_agent.modules.tasks.actor_runner import ActorRunner, collect_manifest
from valuz_agent.modules.tasks.coordination import CoordinationService
from valuz_agent.adapters.agent_resolver import resolve_agent_display_name
from valuz_agent.modules.tasks.events import finalize_task, record_subtask_stopped  # noqa: I001
from valuz_agent.modules.tasks.datastore import (
    TaskDatastore,
    TaskEventDatastore,
    TaskSessionDatastore,
)
from valuz_agent.modules.tasks.live_member_registry import LiveMemberRegistry
from valuz_agent.modules.tasks.mailbox import InboxMsg, mailbox_registry
from valuz_agent.modules.tasks.member_state import (
    reconcile,
)
from valuz_agent.modules.tasks.plan import TaskPlan

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# RecoveryService (ADR-023 Step 3d)
# ---------------------------------------------------------------------------


class RecoveryService:
    """Startup sweep + user stop/resume.

    Registry keystone in ``_recover_one_task``: each resumable member is
    re-seeded via ``registry.add_member`` (no dispatch epoch on the recovery
    branch) BEFORE its actor loop respawns — mirroring ``dispatch_async``.
    """

    def __init__(
        self,
        *,
        registry: LiveMemberRegistry,
        actor_runner: ActorRunner,
        coordination: CoordinationService,
    ) -> None:
        self._members = registry
        self._actor = actor_runner
        self._coordination = coordination

    # ------------------------------------------------------------------
    # Layer 1 (VALUZ-RESUME §5.3): startup recovery
    # ------------------------------------------------------------------

    async def recover_active_tasks(self) -> int:
        """Layer 1 (VALUZ-RESUME §5.3): on host startup, reconcile + resume every
        ``active`` task whose actor loops died with the previous process.

        Only ``active`` tasks are touched — ``paused``/``stopped`` are intentional
        user stops (resume on explicit request), terminal states are done.
        Best-effort + idempotent (re-running converges on current run/node state).
        """
        async with async_unit_of_work(commit=False) as db:
            # Cross-owner boot sweep: capture each task's owner so per-task
            # recovery runs under that owner (downstream reads are owner-scoped
            # by explicit user_id parameters).
            active = [
                (t.id, t.project_id, t.user_id) for t in await TaskDatastore(db).list_active()
            ]
        recovered = 0
        for task_id, project_id, user_id in active:
            try:
                if await self._recover_one_task(task_id, project_id, user_id=user_id):
                    recovered += 1
            except Exception:  # noqa: BLE001
                logger.exception("recover_active_tasks: failed for task %s", task_id)
        if recovered:
            logger.warning(
                "recover_active_tasks: reconciled + re-drove %d active task(s)", recovered
            )
        return recovered

    async def _recover_one_task(
        self,
        task_id: str,
        project_id: str,
        user_id: str,
        *,
        lead_instruction: str | None = None,
    ) -> bool:
        """Reconcile one active task's members + re-drive its lead.

        Used by both Layer 1 (startup) and Layer 2 (user 'resume'). Returns False
        if the task isn't recoverable (gone / no lead run).

        ``lead_instruction`` (Layer 2 only): a free-text user instruction that
        rides along with the resume — appended to the lead's recovery brief in
        the same ``<user-instruction>`` envelope ``inject_into_task`` uses, so
        "回复并恢复" is one atomic step instead of resume-then-hope-the-mailbox
        -delivery-races-the-respawn.
        """

        member_done: list[tuple[str, dict[str, Any]]] = []
        # (session_id, brief, run_dir, agent_slug, subtask_key) — run_dir + slug
        # + key let us spill an over-cap resume brief to a doc before re-injecting
        # it into the member's goal-mode session.
        resume_members: list[tuple[str, str, str, str, str]] = []
        summary: list[str] = []
        lead_session_id: str | None = None

        async with async_unit_of_work() as db:
            task_ds = TaskDatastore(db)
            run_ds = TaskSessionDatastore(db)
            event_ds = TaskEventDatastore(db)
            task = await task_ds.get_task_by_project(user_id, project_id, task_id)
            if task is None or task.status not in ("active", "paused"):
                return False
            runs = await run_ds.list_runs(user_id, task_id)
            lead_run = next((r for r in runs if r.kind == "lead"), None)
            if lead_run is None:
                return False
            lead_session_id = lead_run.session_id

            plan = TaskPlan.from_dict(task.plan)
            plan_dirty = False
            for run in runs:
                if run.kind != "subtask" or run.status not in ("active", "paused"):
                    continue
                ks = await kernel_client.get_session(user_id, run.session_id)
                node = plan.get(run.subtask_key) if run.subtask_key else None
                rec = reconcile(
                    getattr(ks, "status", None) if ks is not None else None,
                    getattr(ks, "stop_reason", None) if ks is not None else None,
                    node_attempts=(node.attempts if node else 0),
                )
                manifest: dict[str, Any] | None = None
                if rec.disposition == "completed":
                    try:
                        manifest = await collect_manifest(
                            run.session_id,
                            Path(run.run_dir) if run.run_dir else Path(),
                            "idle",
                            user_id=user_id,
                        )
                    except Exception:  # noqa: BLE001
                        manifest = {
                            "session_id": run.session_id,
                            "status": "completed",
                            "summary": "",
                        }
                    manifest["agent"] = run.agent_slug
                if rec.run_status:
                    await run_ds.update_run_by_session(
                        session_id=run.session_id, status=rec.run_status, result_manifest=manifest
                    )
                if node is not None and rec.node_status:
                    fields: dict[str, Any] = {"status": rec.node_status}
                    if rec.resume:
                        fields["attempts"] = node.attempts + 1
                    if rec.reason and rec.node_status == "rework":
                        fields["review_feedback"] = rec.reason
                    # ``node`` was looked up BY ``run.subtask_key``, so a
                    # non-None node means the key is a real str.
                    plan.update_node(node.key, **fields)
                    plan_dirty = True
                if rec.deliver_member_done and manifest is not None:
                    member_done.append((run.session_id, manifest))
                if rec.resume:
                    resume_members.append(
                        (
                            run.session_id,
                            run.goal or "",
                            run.run_dir or "",
                            run.agent_slug or "",
                            run.subtask_key or "",
                        )
                    )
                summary.append(f"- {run.subtask_key}({run.agent_slug}): {rec.disposition}")

            if plan_dirty:
                await planning.persist_plan(
                    task_ds,
                    event_ds,
                    task,
                    plan,
                    actor="system",
                    session_id=lead_session_id,
                    user_id=user_id,
                )

        # Evict any stale kernel runtime BEFORE respawning. Load-bearing for
        # pause→resume: the pause interrupt leaves a cancelled SDK client in
        # the kernel's runtime cache, and reusing it makes the resumed turn
        # cancel instantly → auto-finalize blocks the task. Doing it here is
        # race-free (old loop exited, new one not yet built).
        async def _evict_runtime(sid: str) -> None:
            try:
                await kernel_client.cleanup_runtime(sid)
            except Exception:  # noqa: BLE001
                pass

        # Re-drive (outside the DB txn): register the lead mailbox, deliver any
        # completed members' results, respawn resumable members (kernel run_turn
        # on the persisted session), then respawn the lead with a reconcile brief.
        mailbox_registry.register(lead_session_id)
        for member_sid, manifest in member_done:
            mailbox_registry.put(
                lead_session_id,
                InboxMsg(kind="member_done", from_session=member_sid, payload=manifest),
            )
        for member_sid, brief, m_run_dir, m_slug, m_key in resume_members:
            await _evict_runtime(member_sid)
            self._members.add_member(task_id, member_sid)
            resume_prompt = brief or "继续完成你的子任务,完成后会汇报给 lead。"
            # Fence the goal-mode re-injection: an over-cap subtask goal would
            # blow the ``/goal`` payload again on resume — spill it to a doc and
            # re-inject a short pointer instead (same fence as first dispatch).
            if brief and m_run_dir:
                resume_prompt = spill_goal_brief_if_too_long(
                    brief,
                    run_dir=m_run_dir,
                    task_id=task_id,
                    label=f"{m_slug}-{m_key}",
                    is_lead=False,
                )
            asyncio.create_task(
                self._actor.run_actor_loop(
                    session_id=member_sid,
                    initial_prompt=resume_prompt,
                    role="subtask",
                    task_id=task_id,
                    project_id=project_id,
                    user_id=user_id,
                )
            )
        await _evict_runtime(lead_session_id)
        lead_brief = (
            "<system-recovery>\n本任务已被恢复(系统重启或用户恢复)。子任务对账结果:\n"
            + ("\n".join(summary) if summary else "(无在途子任务)")
            + "\n\n请先调用 get_plan 对齐当前状态,然后继续编排:派发未决子任务、"
            "审核 in_review、重试 rework;全部完成后调用 finish_task。\n</system-recovery>"
        )
        if lead_instruction and lead_instruction.strip():
            lead_brief += (
                '\n<user-instruction source="resume">\n'
                + lead_instruction.strip()
                + "\n</user-instruction>\n"
                "用户在恢复任务时附带了上面的指令——它是权威的用户意图,请优先据此调整编排"
                "(必要时 modify_plan / rework)再继续。"
            )
        asyncio.create_task(
            self._actor.run_actor_loop(
                session_id=lead_session_id,
                initial_prompt=lead_brief,
                role="lead",
                task_id=task_id,
                project_id=project_id,
                user_id=user_id,
            )
        )
        return True

    # ------------------------------------------------------------------
    # Layer 2 (VALUZ-RESUME §5.5): user-initiated stop / resume
    # ------------------------------------------------------------------

    async def _interrupt_kernel_session(self, session_id: str, user_id: str) -> None:
        """Best-effort: ask the kernel runtime to stop an in-flight turn.

        Returns silently whether or not a runtime was active — a member parked
        between turns has no live runtime (``interrupt`` returns False), and the
        ``shutdown`` mailbox message is what stops its actor loop instead.
        """
        try:
            await kernel_client.interrupt(user_id, session_id)
        except Exception:  # noqa: BLE001
            logger.warning("interrupt failed for session %s", session_id, exc_info=True)

    async def stop_task(
        self,
        task_id: str,
        project_id: str,
        *,
        target_status: str = "paused",
        user_id: str,
    ) -> bool:
        """Cascade halt → ``paused`` (from active) or ``stopped`` (from
        active/paused; soft-terminal but revivable via resume_task).

        Interrupts lead + members, broadcasts shutdown, parks in-flight runs
        and their ``in_progress`` plan nodes ``→paused``, then flips the task.
        Members are parked identically for both targets. Returns False when
        the task is gone or the transition is illegal.
        """
        async with async_unit_of_work() as db:
            task_ds = TaskDatastore(db)
            run_ds = TaskSessionDatastore(db)
            event_ds = TaskEventDatastore(db)
            task = await task_ds.get_task_by_project(user_id, project_id, task_id)
            if task is None:
                return False
            # pause: only an active task. stop: an active OR already-paused task.
            allowed_from = ("active",) if target_status == "paused" else ("active", "paused")
            if task.status not in allowed_from:
                return False
            runs = await run_ds.list_runs(user_id, task_id)
            lead_session_id: str | None = next(
                (r.session_id for r in runs if r.kind == "lead"), None
            )
            member_sids = [
                r.session_id for r in runs if r.kind == "subtask" and r.status == "active"
            ]
            for sid in member_sids:
                await run_ds.update_run_by_session(session_id=sid, status="paused")
            # Park only the running member's node (``in_progress`` = a live
            # member session, the one we're halting) → ``paused`` so the panel
            # stops spinning it. Leave ``in_review`` (member finished, awaiting
            # the lead's review — parking would lose that) and ``rework``
            # (awaiting re-dispatch) alone. On resume, recovery reconcile flips
            # a parked node back to ``in_progress`` if its run survived;
            # otherwise it stays ``paused`` and is re-dispatchable (ready_keys +
            # resolve_dispatch_node both accept ``paused``).
            plan = TaskPlan.from_dict(task.plan)
            parked = 0
            for node in plan.nodes:
                if node.status == "in_progress":
                    plan.update_node(node.key, status="paused")
                    parked += 1
            if parked:
                await planning.persist_plan(
                    task_ds,
                    event_ds,
                    task,
                    plan,
                    actor="user",
                    session_id=lead_session_id,
                    user_id=user_id,
                )
            if target_status == "stopped":
                # Terminal write — goes through finalize_task so the status
                # flip rides the task_state guard AND ``task.finalized`` is
                # announced (the sandbox-TTL clamp listens on it; the old
                # direct ``update_task`` write here skipped both). The event
                # type stays the raw "stopped" — it drives UI status + timer.
                await finalize_task(
                    db,
                    user_id=user_id,
                    project_id=project_id,
                    task_id=task_id,
                    status="stopped",
                    event_type="stopped",
                    actor="user",
                    payload={"members_paused": len(member_sids)},
                )
            else:
                await task_ds.update_task_status(user_id, task_id, "paused")
                await event_ds.append_event(
                    user_id,
                    project_id,
                    task_id,
                    "paused",  # drives UI status + timer
                    actor="user",
                    payload={"members_paused": len(member_sids)},
                )

        # Cascade interrupt + shutdown (outside the DB txn).
        for sid in member_sids:
            await self._interrupt_kernel_session(sid, user_id=user_id)
        if lead_session_id is not None:
            await self._interrupt_kernel_session(lead_session_id, user_id=user_id)
        self._coordination._broadcast_shutdown(task_id)
        if lead_session_id is not None:

            mailbox_registry.put(lead_session_id, InboxMsg(kind="shutdown"))
        return True

    async def resume_task(
        self,
        task_id: str,
        project_id: str,
        *,
        actor: str = "user",
        user_id: str,
        instruction: str | None = None,
    ) -> dict[str, Any]:
        """Resume a ``paused`` / ``blocked`` / ``stopped`` / ``completed`` task
        (``completed`` = deliberate reopen to supplement its subtasks; only
        ``abandoned`` is hard-terminal, and ``draft`` launches via commit_task).

        Flips the task ``active``, reactivates the lead run row when a prior
        finish marked it completed, then reconciles + respawns through the
        shared ``_recover_one_task`` machine. ``instruction`` rides along into
        the respawned lead's recovery brief and is recorded as ``user_inject``.

        Returns ``{ok, prior_status, resumed|error}`` — a dict rather than a
        bool because the MCP tool needs a human-readable rejection reason.
        """
        from valuz_agent.modules.tasks.task_state import assert_transition

        async with async_unit_of_work() as db:
            task_ds = TaskDatastore(db)
            event_ds = TaskEventDatastore(db)
            run_ds = TaskSessionDatastore(db)
            task = await task_ds.get_task_by_project(user_id, project_id, task_id)
            if task is None:
                return {"ok": False, "error": f"task {task_id!r} not found", "prior_status": None}
            prior_status = task.status
            # ``failed`` is a LEGACY status (pre-dates folding task failure
            # into ``blocked``); old rows still carry it and were stranded —
            # no action bar, resume rejected. Treat it exactly like blocked.
            if prior_status not in ("paused", "blocked", "stopped", "completed", "failed"):
                return {
                    "ok": False,
                    "error": (
                        f"resume_task rejected: task is {prior_status!r}, only "
                        "'paused', 'blocked', 'stopped', or 'completed' tasks "
                        "(or legacy 'failed' rows) can be resumed. 'abandoned' "
                        "is hard-terminal (draft discarded, nothing to revive) "
                        "and 'draft' must be launched with commit_task. "
                        "Reopening a 'completed' task is for supplementing/"
                        "adjusting its subtasks; a genuinely new goal should "
                        "be a fresh follow-up task."
                    ),
                    "prior_status": prior_status,
                }
            # Belt-and-suspenders: confirm the transition the state machine
            # accepts. paused/blocked/stopped/completed → active are all legal.
            # Legacy ``failed`` is outside the enum — ``update_task_status``
            # tolerates unknown *source* statuses precisely for this case, so
            # skip the formal check there.
            if prior_status != "failed":
                assert_transition(prior_status, "active")
            await task_ds.update_task_status(user_id, task_id, "active")
            # When reviving a stopped OR completed task: finish_task previously
            # marked the lead run as "completed" and broadcast shutdown to
            # members. _recover_one_task respawns the lead unconditionally, but
            # the run row still showing "completed" would lie about reality —
            # fix it so listings + UI reflect the live state. Legacy ``failed``
            # rows may carry any run status — normalise them the same way.
            if prior_status in ("stopped", "completed", "failed"):
                runs = await run_ds.list_runs(user_id, task_id)
                lead_run = next((r for r in runs if r.kind == "lead"), None)
                if lead_run is not None and lead_run.status != "active":
                    await run_ds.update_run_by_session(
                        session_id=lead_run.session_id,
                        status="active",
                        ended_at=None,
                    )
            await event_ds.append_event(
                user_id,
                project_id,
                task_id,
                "resumed",
                actor=actor,
                payload={"from": prior_status},
            )
            if instruction and instruction.strip():
                # Timeline record of what the user asked for alongside the
                # resume — same event type the chat-inject path appends, so
                # the detail page renders both uniformly.
                await event_ds.append_event(
                    user_id,
                    project_id,
                    task_id,
                    "user_inject",
                    actor=actor,
                    payload={"text": instruction.strip(), "via": "resume"},
                )
        # Clear any open "task failed" notification — the user is dealing with
        # it now, so it mustn't keep the badge lit (docs/design/notifications.md).
        try:
            from valuz_agent.modules.notifications.service import notification_service

            await notification_service.resolve_task(user_id or "", task_id)
        except Exception:  # noqa: BLE001
            logger.warning("resume_task: failed to clear failure notification", exc_info=True)

        ok = await self._recover_one_task(
            task_id, project_id, user_id=user_id, lead_instruction=instruction
        )
        return {"ok": ok, "prior_status": prior_status, "resumed": ok}

    async def stop_member(self, session_id: str, user_id: str) -> bool:
        """User-initiated single-member stop (task stays ``active``).

        Interrupts one subtask session, notifies the lead with a
        ``member_done(status=cancelled)`` so it doesn't wait forever, flips the
        run ``→rejected`` and the plan node ``→rework``. The lead decides next
        (redispatch / modify_plan / finish) on its next ``get_plan``.
        """

        async with async_unit_of_work() as db:
            run_ds = TaskSessionDatastore(db)
            task_ds = TaskDatastore(db)
            event_ds = TaskEventDatastore(db)
            run = await run_ds.get_run(session_id)
            if run is None or run.kind != "subtask":
                return False
            task_id = run.task_id or ""
            project_id = run.project_id
            lead_session_id = run.dispatched_by or ""
            subtask_key = run.subtask_key
            agent_slug = run.agent_slug
            await run_ds.update_run_by_session(session_id=session_id, status="rejected")
            if subtask_key:
                task = await task_ds.get_task_by_project(user_id, project_id, task_id)
                if task is not None:
                    plan = TaskPlan.from_dict(task.plan)
                    if plan.get(subtask_key) is not None:
                        plan.update_node(
                            subtask_key,
                            status="rework",
                            review_feedback="用户手动停止了该子任务",
                        )
                        await planning.persist_plan(
                            task_ds,
                            event_ds,
                            task,
                            plan,
                            actor="user",
                            session_id=lead_session_id or None,
                            user_id=user_id,
                        )
            await record_subtask_stopped(
                event_ds,
                user_id=user_id,
                project_id=project_id,
                task_id=task_id,
                session_id=session_id,
                agent_slug=agent_slug,
                agent_name=await resolve_agent_display_name(project_id, agent_slug, user_id),
                subtask_key=subtask_key,
            )

        await self._interrupt_kernel_session(session_id, user_id=user_id)
        self._members.discard_member(task_id, session_id)
        if lead_session_id:
            mailbox_registry.put(
                lead_session_id,
                InboxMsg(
                    kind="member_done",
                    from_session=session_id,
                    payload={
                        "agent": agent_slug,
                        "status": "cancelled",
                        "summary": "用户停止了该子任务",
                        "artifacts": [],
                    },
                ),
            )
        return True
