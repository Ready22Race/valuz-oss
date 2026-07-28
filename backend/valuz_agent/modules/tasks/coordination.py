"""CoordinationService — lead ↔ member coordination (ADR-023, Step 3b).

Peeled verbatim out of ``TaskOrchestrator``. Owns the in-turn / between-turn
coordination surface:

  * :meth:`await_member_results` — in-turn mailbox drain (8s heartbeat slices,
    user_inject preemption).
  * :meth:`_heartbeat_pending` — bad-case#3 backstop (reconcile a member whose
    kernel session went terminal but whose member_done never reached the lead).
  * :meth:`notify_lead_member_idle` — the role=="subtask" run-actor-loop
    callback: post a ``member_done`` to the lead's inbox after a member turn.
  * :meth:`lead_idle_with_no_pending` — the role=="lead" run-actor-loop check:
    True when the lead has nothing left to wait for.
  * :meth:`_broadcast_shutdown` — the atomic shutdown primitive (single
    ``drain_members`` pop → per-member shutdown put).

Holds no task state — it receives the shared :class:`LiveMemberRegistry` by
constructor injection (the same instance the composition root wires into every
other task service) for ``has_live_members`` / ``dispatch_started_at`` /
``drain_members``.

Text delivery (lead↔member ``send_to_member``, chat→task ``inject_into_task``,
``notify_lead_goal_revised``) is NOT surfaced here. An earlier ADR proposed
folding ``messaging.py`` into this class; the resulting delegators never had a
caller — every real call site (dispatch-MCP handlers, task routes) imports
``tasks.messaging`` directly — and they silently rotted out of signature sync
until mypy flagged them. They were deleted 2026-07; call ``messaging.*``.

CRITICAL invariant (``_broadcast_shutdown``): the drain + per-member shutdown
``put`` loop must stay SYNCHRONOUS and contiguous — no ``await`` may separate the
single atomic ``registry.drain_members`` pop from the shutdown puts, or a member
spawned concurrently by ``dispatch_async`` could be dropped.
"""

# ruff: noqa: I001
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, cast

from valuz_agent.adapters.agent_resolver import resolve_agent_display_name
from valuz_agent.adapters.data_reader import data_reader
from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.tasks import planning
from valuz_agent.modules.tasks.actor_runner import _NON_REVIEWABLE_DONE, collect_manifest
from valuz_agent.modules.tasks.datastore import (
    TaskDatastore,
    TaskEventDatastore,
    TaskSessionDatastore,
)
from valuz_agent.modules.tasks.live_member_registry import LiveMemberRegistry
from valuz_agent.modules.tasks.plan import PlanError, TaskPlan

logger = logging.getLogger(__name__)

# Heartbeat slice for await_member_results: how often the lead reconciles
# in-flight members against their kernel session while waiting (VALUZ-RESUME §5.4).
_HEARTBEAT_S = 8.0

# Max seconds a SINGLE await_members call parks. await is designed to be LOOPED
# (the still_running hint + inbox-notice drive prompt re-await), so one call
# never needs to wait longer — and it MUST stay under the MCP client's tool-call
# ceiling (codex aborts a tool call at its ``tool_timeout_sec``) so a healthy
# wait is never mis-reported as a transport failure. The harness MCP servers set
# that ceiling to this value + a margin (see
# capability_resolver._INTERNAL_MCP_TOOL_TIMEOUT_SEC). A model-supplied
# ``timeout_s`` above this is clamped.
_MAX_AWAIT_WINDOW_S = 600.0


class CoordinationService:
    """Lead ↔ member coordination.

    Constructed once at the composition root with the shared registry. The
    orchestrator exposes ``await_member_results`` from here, and the class is
    bound into the ActorRunner as its
    :class:`~valuz_agent.modules.tasks.actor_runner.ActorCoordinator` — the two
    role callbacks (:meth:`notify_lead_member_idle` /
    :meth:`lead_idle_with_no_pending`) are that protocol, typed, so a signature
    drift is a mypy error rather than a runtime surprise.
    """

    def __init__(self, *, registry: LiveMemberRegistry) -> None:
        self._members = registry

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
        user_id: str,
    ) -> dict[str, Any]:
        """Block (inside the lead's turn) until dispatched members finish.

        v0.14 real-time dispatch (see decision doc §14): the lead calls this
        right after ``dispatch``-ing one or more subtasks. Drains the lead's
        mailbox for ``member_done`` messages (the same channel the actor-loop
        fallback uses *between* turns — but here we consume it *within* the
        turn, so the lead reviews results without a between-turn round-trip).

        ``keys``: subtask keys to wait for; ``None`` = all currently
        outstanding nodes (plan status in_progress/in_review). ``mode``:
        ``all`` waits for every target key, ``any`` returns on the first.
        ``timeout_s``: on expiry, return whatever was collected plus
        ``pending`` (so a stuck member can't hang the lead forever).
        """
        from valuz_agent.modules.tasks.mailbox import mailbox_registry

        # Ensure the lead inbox exists so ``get`` blocks for member_done
        # instead of raising KeyError (which would return empty instantly and
        # make the lead think members are stuck). ``dispatch`` already
        # registers it; this is belt-and-suspenders. Idempotent.
        mailbox_registry.register(lead_session_id)

        # Load the plan + the set of subtask keys that currently have a
        # dispatched, in-flight member (an "active" subtask run). We need both to
        # (a) resolve the target set when ``keys`` is omitted and (b) guard the
        # wait below. ``dispatch_async`` records the run as ``active``
        # synchronously (create_run) before it returns, so this DB view is
        # authoritative the moment a real dispatch has happened.
        async with async_unit_of_work(commit=False) as db:
            row = await TaskDatastore(db).get_task_by_project(user_id, project_id, task_id)
            live_keys = {
                r.subtask_key
                for r in await TaskSessionDatastore(db).list_runs(user_id, task_id)
                if r.kind == "subtask" and r.subtask_key and r.status == "active"
            }
        plan = TaskPlan.from_dict(row.plan) if row else TaskPlan()

        # Resolve the target set from the plan when keys are not given.
        if keys:
            target: set[str] = {k for k in keys if k}
        else:
            target = {n.key for n in plan.nodes if n.status in ("in_progress", "in_review")}

        # Precondition (VALUZ: "planned-but-never-dispatched, then await" trap):
        # at least one target key must have a live member to wait on. Awaiting a
        # key with no dispatched member can only ever burn the full timeout
        # waiting for a ``member_done`` that can never arrive — which is exactly
        # what strands a lead that re-planned but forgot to ``dispatch``. Return
        # immediately with actionable guidance instead of blocking.
        awaitable = (target & live_keys) if target else live_keys
        if not awaitable:
            requested = sorted(target)
            return {
                "error": "no_dispatched_members",
                "message": (
                    "await_members: nothing to wait for — no dispatched member is in "
                    "flight"
                    + (f" for keys {requested}" if requested else "")
                    + ". A member exists only after you dispatch its subtask."
                ),
                "hint": (
                    "Call dispatch(subtask_key=...) for a ready subtask BEFORE "
                    "await_members. Use get_plan to inspect statuses."
                ),
                "ready_keys": plan.ready_keys(),
                "results": [],
                "pending": requested,
                "collected": 0,
                "timed_out": False,
            }

        loop = asyncio.get_running_loop()
        # Default cap so a member that dies without a member_done can't hang
        # the lead indefinitely (the actor loop posts member_done even on
        # terminal status, so this is a backstop, not the common path).
        # Clamp the per-call wait to one window unit (_MAX_AWAIT_WINDOW_S). A
        # larger model-supplied timeout_s doesn't buy anything — await loops — and
        # would risk exceeding the codex tool-call ceiling, turning a healthy wait
        # into a "timed out awaiting tools/call" transport failure.
        requested = timeout_s if timeout_s is not None else _MAX_AWAIT_WINDOW_S
        effective_timeout = min(requested, _MAX_AWAIT_WINDOW_S)
        deadline = loop.time() + effective_timeout
        collected: dict[str, dict[str, Any]] = {}
        # VALUZ-CHATPLAN S5: if a user-injected ``message`` arrives in the
        # lead mailbox while we wait, BREAK OUT immediately with whatever has
        # been collected so far + the injection — the lead needs to react
        # (often by ``modify_plan``/``dispatch``-ing extra work) before
        # continuing to wait. Was previously silently dropped (``continue``),
        # which delayed inject by up to ``timeout_s``.
        user_inject: dict[str, Any] | None = None
        # Set when the wait broke early because EVERY pending member is parked
        # on a question for the user (requires_action) — waiting the full
        # timeout is pure waste when nothing can move without the user.
        awaiting_user_break = False
        pending_probe: list[dict[str, Any]] = []

        while True:
            if mode == "all" and target and target.issubset(collected.keys()):
                break
            if mode == "any" and collected:
                break
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            # Chop the wait into ~8s heartbeat slices (VALUZ-RESUME §5.4): on each
            # slice expiry, reconcile in-flight members whose kernel session went
            # terminal but whose member_done never reached the mailbox (bad-case
            # #3 online window). Synthesize their result so the lead doesn't hang.
            slice_timeout = min(_HEARTBEAT_S, remaining)
            try:
                msg = await mailbox_registry.get(lead_session_id, timeout=slice_timeout)
            except TimeoutError:
                pending_now = (target - set(collected.keys())) if target else set()
                collected.update(
                    await self._heartbeat_pending(
                        task_id=task_id,
                        project_id=project_id,
                        pending_keys=pending_now,
                        user_id=user_id,
                    )
                )
                # Parked-member probe: a member sitting on an AskUserQuestion
                # keeps its kernel session ``running``, so from here it is
                # indistinguishable from a long tool call — unless we ask the
                # decision inbox. When EVERY still-pending member is parked on
                # user input, break out now with that state instead of burning
                # the rest of the timeout (nothing moves until the user
                # answers; the lead gets to react — do other work or end the
                # turn and be woken by the eventual member_done).
                still_pending = (target - set(collected.keys())) if target else set()
                if still_pending:
                    probe = await self._probe_pending_members(
                        task_id=task_id, pending_keys=still_pending, user_id=user_id
                    )
                    if (
                        probe
                        and len(probe) == len(still_pending)
                        and all(p.get("state") == "awaiting_user" for p in probe)
                    ):
                        pending_probe = probe
                        awaiting_user_break = True
                        break
                continue
            except KeyError:
                break
            if msg.kind == "shutdown":
                # Put it BACK before leaving. ``await_member_results`` runs
                # inside the lead's turn and drains the same inbox the actor
                # loop reads between turns, so consuming a shutdown here would
                # swallow the only signal that tells the loop to stop — the
                # lead would finish this turn and keep looping on a task that
                # ``stop_task`` / ``finish_task`` already halted. (It survived
                # this long only because ``stop_task`` ALSO interrupts the
                # kernel turn, which happens to end the loop by another route;
                # ``finish_task``'s own broadcast had no such backstop.)
                mailbox_registry.put(lead_session_id, msg)
                break
            if msg.kind in ("text", "revise_goal"):
                # VALUZ-CHATPLAN S5: user inject via chat, OR a goal revision
                # (both are authoritative user intent). Capture + break so the
                # lead can react in this turn instead of waiting for a member_done
                # that may not arrive for minutes.
                user_inject = {
                    "text": msg.text,
                    "from_session": msg.from_session,
                }
                break
            if msg.kind != "member_done":
                continue
            from_sid = msg.from_session
            async with async_unit_of_work(commit=False) as db:
                run = await TaskSessionDatastore(db).get_run(from_sid)
            sk = run.subtask_key if (run and run.subtask_key) else from_sid
            m = msg.payload or {}
            # Member idle ≠ done: flip the node to in_review for the lead's
            # review_subtask (the actor-loop fallback does the same). ONLY for
            # a delivering member — a failed/cancelled member_done has no work
            # to review; its node is already parked in ``rework`` and flipping
            # it back would present a dead run as a pending deliverable.
            if run and run.subtask_key and str(m.get("status") or "") not in _NON_REVIEWABLE_DONE:
                await planning.mark_in_review(
                    task_id=task_id,
                    project_id=project_id,
                    member_session_id=from_sid,
                    user_id=user_id,
                )
            collected[sk] = {
                "subtask_key": run.subtask_key if (run and run.subtask_key) else None,
                "session_id": from_sid,
                "agent": m.get("agent", ""),
                "status": m.get("status", ""),
                "summary": m.get("summary", ""),
                "artifacts": m.get("artifacts", []),
            }

        pending = sorted(target - set(collected.keys())) if target else []
        out: dict[str, Any] = {
            "results": list(collected.values()),
            "pending": pending,
            "collected": len(collected),
            "timed_out": bool(pending) and mode == "all" and not awaiting_user_break,
        }
        if pending:
            # Tell the lead what the pending members are actually DOING — a
            # bare key list left it unable to distinguish "still building" from
            # "dead", which is how leads end up stopping healthy tasks.
            if not pending_probe:
                pending_probe = await self._probe_pending_members(
                    task_id=task_id, pending_keys=set(pending), user_id=user_id
                )
            out["pending_status"] = pending_probe
            if awaiting_user_break:
                out["awaiting_user"] = True
                out["hint"] = (
                    "Every pending member is paused on a question for the USER "
                    "(it appears in the user's decision inbox). Do NOT re-call "
                    "await_members right away — it will return this same state. "
                    "Either work on other ready subtasks (get_plan → dispatch), "
                    "or end your turn: you will be woken with a member_done once "
                    "the member gets its answer and finishes."
                )
            elif any(p.get("state") == "running" for p in pending_probe):
                # Members still running is the COMMON early-return case: mode
                # "any" returns the instant nothing is collected, so ``timed_out``
                # stays False (it requires mode "all"). The old guard gated this
                # hint on ``timed_out`` and therefore NEVER fired for the default
                # mode — which is exactly how a lead, told only a bare
                # ``pending:[k] state:running``, went silent for minutes instead
                # of re-awaiting (the queued member_done then sat unread until the
                # next await). Fire whenever a pending member is alive, regardless
                # of mode / timed_out.
                out["still_running"] = True
                out["hint"] = (
                    "Pending members with state 'running' are ALIVE and still "
                    "working — a long tool call (research, build, tests) easily "
                    "exceeds this wait. Do NOT treat them as dead and do NOT stop "
                    "the task. Call await_members again right away (the wait is a "
                    "fixed window — just loop it, a bigger timeout_s won't help): "
                    "any member that finishes meanwhile is already queued in your "
                    "inbox and returns to you instantly. Do not pause to reason in "
                    "between."
                )
        if user_inject is not None:
            # Surface the inject to the lead so it can decide how to respond
            # (typically: modify_plan + dispatch extra, or send to an in-flight
            # member, or stop a misdirected subtask). The user-instruction
            # wrap ``<user-instruction source="chat">`` already provides
            # framing inside ``text`` for the LLM.
            out["user_inject"] = user_inject
            out["preempted_by_inject"] = True
        return out

    async def _heartbeat_pending(
        self,
        *,
        task_id: str,
        project_id: str,
        pending_keys: set[str],
        user_id: str,
    ) -> dict[str, dict[str, Any]]:
        """Backstop for bad-case #3 (VALUZ-RESUME §5.4): a member whose kernel
        session went terminal but whose ``member_done`` never reached the lead's
        mailbox (delivery window / crash before finalize).

        For each still-pending subtask key, check the kernel session; if terminal
        (end_turn → completed, error → failed) persist the run/node disposition
        and return a synthesized collection entry so the lead's wait completes.
        ``running``/resumable members are left pending (resume is a restart
        concern, not an online-wait one).
        """
        if not pending_keys:
            return {}
        from valuz_agent.modules.tasks.recovery import classify_member

        out: dict[str, dict[str, Any]] = {}
        async with async_unit_of_work() as db:
            run_ds = TaskSessionDatastore(db)
            task_ds = TaskDatastore(db)
            event_ds = TaskEventDatastore(db)
            runs_by_key = {
                r.subtask_key: r
                for r in await run_ds.list_runs(user_id, task_id)
                if r.kind == "subtask" and r.subtask_key and r.status == "active"
            }
            if not any(k in runs_by_key for k in pending_keys):
                return {}  # nothing in-flight for these keys — don't touch the plan
            task = await task_ds.get_task_by_project(user_id, project_id, task_id)
            plan = TaskPlan.from_dict(task.plan) if task is not None else None
            plan_dirty = False
            for key in pending_keys:
                run = runs_by_key.get(key)
                if run is None:
                    continue
                ks = await data_reader().get_session(user_id, run.session_id)
                if getattr(ks, "status", None) == "running":
                    continue  # genuinely in flight — keep waiting
                disp = classify_member(
                    getattr(ks, "status", None) if ks is not None else None,
                    getattr(ks, "stop_reason", None) if ks is not None else None,
                )
                node = plan.get(key) if plan is not None else None
                if disp == "completed":
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
                    await run_ds.update_run_by_session(
                        session_id=run.session_id, status="completed", result_manifest=manifest
                    )
                    if node is not None and node.status in ("in_progress", "rework"):
                        plan.update_node(key, status="in_review")  # type: ignore[union-attr]
                        plan_dirty = True
                    out[key] = {
                        "subtask_key": key,
                        "session_id": run.session_id,
                        "agent": run.agent_slug,
                        "status": manifest.get("status", "completed"),
                        "summary": manifest.get("summary", ""),
                        "artifacts": manifest.get("artifacts", []),
                    }
                elif disp == "failed":
                    await run_ds.update_run_by_session(session_id=run.session_id, status="archived")
                    if node is not None:
                        plan.update_node(  # type: ignore[union-attr]
                            key,
                            status="rework",
                            review_feedback="member session errored (heartbeat)",
                        )
                        plan_dirty = True
                    # Emit ``subtask_failed`` like every other member-failure
                    # path (_finalize_actor / dispatcher). Without this a
                    # heartbeat-detected failure archived the run + reworked the
                    # node INVISIBLY — no timeline row, no attention signal; the
                    # user just saw the subtask silently blink active→pending.
                    # Stamp ``agent_name`` (established rule) so the frontend
                    # doesn't race an async member-list join.
                    agent_name = await resolve_agent_display_name(
                        project_id, run.agent_slug or "", user_id
                    )
                    await event_ds.append_event(
                        user_id,
                        project_id=project_id,
                        task_id=task_id,
                        type="subtask_failed",
                        actor=run.agent_slug or "",
                        session_id=run.session_id,
                        payload={
                            "agent_name": agent_name,
                            "subtask_key": key,
                            "status": "failed",
                            "summary": "member session errored",
                            "reason": "heartbeat_detected",
                        },
                    )
                    out[key] = {
                        "subtask_key": key,
                        "session_id": run.session_id,
                        "agent": run.agent_slug,
                        "status": "failed",
                        "summary": "member session errored",
                        "artifacts": [],
                    }
            if plan_dirty and plan is not None and task is not None:
                task.plan = plan.to_dict()
                await task_ds.update_task(task)
                await planning.emit_plan_update(
                    event_ds,
                    project_id=project_id,
                    task_id=task_id,
                    plan=plan,
                    actor="system",
                    session_id=None,
                    user_id=user_id,
                )
        return out

    async def _probe_pending_members(
        self,
        *,
        task_id: str,
        pending_keys: set[str],
        user_id: str,
    ) -> list[dict[str, Any]]:
        """Best-effort live status of still-pending members — READ ONLY.

        Unlike ``_heartbeat_pending`` this never touches the plan or the runs;
        it answers the one question a waiting lead cannot otherwise answer:
        *is this silent member alive?* Three observable states:

          * ``awaiting_user`` — the member is parked mid-turn on an
            AskUserQuestion (pending decision-inbox entry); nothing moves until
            the user answers.
          * ``running`` — genuinely working (possibly a long tool call).
          * anything else / ``unknown`` — the kernel status as-is (terminal
            states are normally reconciled by the heartbeat before this runs).
        """
        if not pending_keys:
            return []
        try:
            async with async_unit_of_work(commit=False) as db:
                runs_by_key = {
                    r.subtask_key: r
                    for r in await TaskSessionDatastore(db).list_runs(cast(str, user_id), task_id)
                    if r.kind == "subtask" and r.subtask_key and r.status == "active"
                }
        except Exception:  # noqa: BLE001
            logger.debug("probe_pending_members: run listing failed", exc_info=True)
            return []
        asks = await self._pending_asks_by_session(user_id)
        out: list[dict[str, Any]] = []
        for key in sorted(pending_keys):
            run = runs_by_key.get(key)
            if run is None:
                continue
            kernel_status: str | None = None
            try:
                ks = await data_reader().get_session(cast(str, user_id), run.session_id)
                kernel_status = getattr(ks, "status", None) if ks is not None else None
            except Exception:  # noqa: BLE001
                logger.debug(
                    "probe_pending_members: session read failed for %s",
                    run.session_id,
                    exc_info=True,
                )
            question = asks.get(run.session_id)
            entry: dict[str, Any] = {
                "subtask_key": key,
                "session_id": run.session_id,
                "agent": getattr(run, "agent_slug", "") or "",
                "state": "awaiting_user" if question is not None else (kernel_status or "unknown"),
            }
            if question:
                entry["question"] = question
            out.append(entry)
        return out

    @staticmethod
    async def _pending_asks_by_session(user_id: str | None) -> dict[str, str]:
        """Map session_id → first pending clarifying-question text, from the
        decision inbox. Best-effort: an unwired aggregator (tests, early boot)
        just means no ask detection, never a failed await."""
        try:
            # Lazy import — decisions is a sibling MODULE (its service API, not
            # its datastore), so this is a sanctioned cross-module call; the
            # import stays lazy only to keep the boot import graph flat.
            # Best-effort by design: an unwired aggregator raises, and this
            # method must degrade to "no ask detected", never fail the await.
            from valuz_agent.modules.decisions.aggregator import get_decision_aggregator

            entries = await get_decision_aggregator().snapshot(user_id or "")
        except Exception:  # noqa: BLE001
            return {}
        out: dict[str, str] = {}
        for e in entries:
            if e.session_id in out:
                continue
            questions = (e.question_payload or {}).get("questions") or []
            first = questions[0] if questions else {}
            text = str(first.get("question") or "").strip() if isinstance(first, dict) else ""
            out[e.session_id] = text[:200] or "(question pending)"
        return out

    # ------------------------------------------------------------------
    # actor-loop role callbacks (driven by ActorRunner via the bound host)
    # ------------------------------------------------------------------

    async def notify_lead_member_idle(
        self, session_id: str, status: str, user_id: str
    ) -> None:
        """After a member turn, push a member_done message to its lead's inbox.

        Also appends a ``subtask_reported`` task event so the timeline shows
        that the member reported back. Best-effort — a missing lead inbox (lead
        already finished) just means the mailbox message is dropped.

        The event type used to be ``subtask_message`` with a
        ``payload.direction`` discriminator shared with the lead→member
        direction, so the timeline could not tell "the lead said something" from
        "a member finished a round of work" without reading the payload. They
        are different events and now have different types; rows written before
        2026-07 keep the old type (the log is append-only and is not rewritten).
        """
        from valuz_agent.modules.tasks.mailbox import InboxMsg, mailbox_registry

        async with async_unit_of_work() as db:
            run_ds = TaskSessionDatastore(db)
            event_ds = TaskEventDatastore(db)
            run = await run_ds.get_run(session_id)
            if run is None:
                return
            lead_session_id = run.dispatched_by or ""
            run_dir = Path(run.run_dir) if run.run_dir else Path()
            since = self._members.dispatch_started_at(session_id)
            manifest = await collect_manifest(
                session_id, run_dir, status, since_epoch=since, user_id=user_id
            )
            manifest["agent"] = run.agent_slug
            # Stamp the display name at emit time (established rule): the
            # frontend renders ``payload.agent_name`` directly instead of
            # joining the slug against an async members list, which races the
            # load and misses agents removed since.
            agent_name = await resolve_agent_display_name(
                run.project_id, run.agent_slug or "", user_id
            )
            await event_ds.append_event(
                user_id,
                project_id=run.project_id,
                task_id=run.task_id or "",
                type="subtask_reported",
                actor=run.agent_slug,
                session_id=session_id,
                payload={
                    "agent_name": agent_name,
                    "summary": manifest.get("summary", ""),
                    "status": status,
                },
            )

        # Mailbox delivery on the event loop (asyncio.Queue is not thread-safe).
        if lead_session_id:
            mailbox_registry.put(
                lead_session_id,
                InboxMsg(
                    kind="member_done",
                    from_session=session_id,
                    payload=manifest,
                ),
            )

    async def lead_idle_with_no_pending(
        self, task_id: str, project_id: str, user_id: str
    ) -> bool:
        """True when a lead has nothing left to wait for after a turn.

        The actor loop normally parks on the mailbox for LEAD_IDLE_TTL_S between
        turns to catch ``member_done`` / follow-ups. But a lead only has a reason
        to wait if it has a member in flight OR an unresolved plan node still to
        drive (``TaskPlan.unresolved_keys`` — the shared predicate, ``paused``
        included). When neither holds, the lead is done — break now so
        ``_finalize_actor`` closes the task immediately instead of after 30min.
        """
        if self._members.has_live_members(task_id):
            return False  # a member is still running — keep waiting for its result
        async with async_unit_of_work(commit=False) as db:
            task = await TaskDatastore(db).get_task_by_project(
                user_id, project_id, task_id
            )
            if task is None or task.status != "active":
                return True  # already closed (finish_task/stop) — let the loop end
            try:
                plan = TaskPlan.from_dict(task.plan)
            except PlanError:
                return True
            return not plan.unresolved_keys()

    # ------------------------------------------------------------------
    # shutdown broadcast — the atomic shutdown primitive
    # ------------------------------------------------------------------

    def _broadcast_shutdown(self, task_id: str) -> None:
        """Tell every still-running member of a task to finalize after its turn."""
        from valuz_agent.modules.tasks.mailbox import InboxMsg, mailbox_registry

        for member_sid in self._members.drain_members(task_id):
            mailbox_registry.put(member_sid, InboxMsg(kind="shutdown"))


__all__ = ["CoordinationService"]
