"""User-interrupt semantics + await liveness (the case-A/B/C fixes).

Covers the chain that previously mislabelled a user-stopped member as a task
failure and left the lead blind while waiting:

  * ``_resolve_turn_status`` — a cancellation stop_reason (``user_interrupt`` /
    ``interrupted``) resolves to the loop-local ``"interrupted"``, not
    ``"terminated"``.
  * actor loop — an interrupted member breaks WITHOUT the per-turn lead notify
    (finalize owns the single ``member_done(cancelled)``).
  * ``member_done`` consumption — a failed/cancelled result must NOT flip the
    plan node back to ``in_review``.
  * ``_finalize_interrupted_member`` — converges with ``stop_member`` (run →
    rejected, node → rework, ``subtask_stopped`` event, one member_done) and
    leaves non-``active`` runs (stop_member / stop_task already recorded) alone.
  * ``await_member_results`` — timeout results carry ``pending_status`` (alive
    vs awaiting user) and break early when every pending member is parked on a
    user question.
  * ``finish_task(stopped)`` — rejected while members are live unless forced.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from valuz_agent.modules.tasks import planning
from valuz_agent.modules.tasks.actor_runner import (
    ActorRunner,
    _resolve_turn_status,
)
from valuz_agent.modules.tasks.mailbox import InboxMsg, mailbox_registry
from valuz_agent.modules.tasks.orchestrator import TaskOrchestrator
from valuz_agent.modules.tasks.member_state import classify_member

LOCAL_USER_ID = "local-test-owner"


# ---------------------------------------------------------------------------
# classification — user_interrupt is intent, not failure
# ---------------------------------------------------------------------------


def test_classify_member_user_interrupt_is_resumable() -> None:
    assert classify_member("idle", {"type": "error", "category": "user_interrupt"}) == "resume"


def test_resolve_turn_status_cancellation_categories() -> None:
    for category in ("user_interrupt", "interrupted"):
        sess = SimpleNamespace(status="idle", stop_reason={"type": "error", "category": category})
        assert _resolve_turn_status(sess) == "interrupted"
    # attr-style stop_reason too
    sess = SimpleNamespace(
        status="idle", stop_reason=SimpleNamespace(type="error", category="user_interrupt")
    )
    assert _resolve_turn_status(sess) == "interrupted"


def test_resolve_turn_status_real_errors_still_terminate() -> None:
    sess = SimpleNamespace(
        status="idle", stop_reason={"type": "error", "category": "execution_error"}
    )
    assert _resolve_turn_status(sess) == "terminated"
    # No category at all → still a failure.
    assert (
        _resolve_turn_status(SimpleNamespace(status="idle", stop_reason={"type": "error"}))
        == "terminated"
    )


# ---------------------------------------------------------------------------
# actor loop — interrupted member breaks silently (finalize owns the notify)
# ---------------------------------------------------------------------------


async def test_interrupted_member_breaks_without_per_turn_notify() -> None:
    orch = TaskOrchestrator()
    notified: list[str] = []
    finalized: list[str] = []

    async def fake_turn(session_id: str, content: str, user_id: str | None = None) -> str:
        return "interrupted"

    async def fake_notify(session_id: str, status: str, user_id: str | None = None) -> None:
        notified.append(status)

    async def fake_finalize(**kwargs: object) -> None:
        finalized.append(str(kwargs["final_status"]))

    orch.actor.run_turn = fake_turn  # type: ignore[method-assign]
    orch.coordination.notify_lead_member_idle = fake_notify  # type: ignore[method-assign]
    orch.lifecycle.finalize_actor = fake_finalize  # type: ignore[method-assign]

    await asyncio.wait_for(
        orch.actor.run_actor_loop(
            session_id="mem-int-1",
            initial_prompt="do it",
            role="subtask",
            task_id="t1",
            project_id="w1",
            user_id=LOCAL_USER_ID,
        ),
        timeout=2.0,
    )
    # No per-turn notify (would double-deliver next to finalize's
    # member_done(cancelled)); the loop broke after one turn and finalized
    # with the interrupted status.
    assert notified == []
    assert finalized == ["interrupted"]


async def test_lead_loop_member_done_cancelled_skips_mark_in_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancelled/terminated member_done must not flip the node to in_review."""
    orch = TaskOrchestrator()
    marked: list[str] = []
    turns = 0

    async def fake_turn(session_id: str, content: str, user_id: str | None = None) -> str:
        nonlocal turns
        turns += 1
        return "idle"

    async def fake_finalize(**kwargs: object) -> None:
        return None

    async def fake_mark(**kwargs: object) -> None:
        marked.append(str(kwargs["member_session_id"]))

    orch.actor.run_turn = fake_turn  # type: ignore[method-assign]
    orch.lifecycle.finalize_actor = fake_finalize  # type: ignore[method-assign]
    monkeypatch.setattr(planning, "mark_in_review", fake_mark)

    mailbox_registry.register("lead-int-1")
    mailbox_registry.put(
        "lead-int-1",
        InboxMsg(
            kind="member_done",
            from_session="mem-cancelled",
            payload={"status": "cancelled", "summary": "用户中断了该子任务"},
        ),
    )
    mailbox_registry.put(
        "lead-int-1",
        InboxMsg(kind="member_done", from_session="mem-ok", payload={"status": "idle"}),
    )
    mailbox_registry.put("lead-int-1", InboxMsg(kind="shutdown"))

    await asyncio.wait_for(
        orch.actor.run_actor_loop(
            session_id="lead-int-1",
            initial_prompt="brief",
            role="lead",
            task_id="t1",
            project_id="w1",
            user_id=LOCAL_USER_ID,
        ),
        timeout=2.0,
    )
    # Only the delivering member got an in_review flip.
    assert marked == ["mem-ok"]


def test_format_member_done_failure_guidance() -> None:
    msg = InboxMsg(
        kind="member_done",
        from_session="s1",
        payload={"status": "cancelled", "summary": "用户中断了该子任务", "agent": "dev"},
    )
    text = ActorRunner._format_member_done(msg)
    assert "did NOT deliver" in text
    assert "review_subtask" not in text.split("</member-result>")[1]
    ok = InboxMsg(kind="member_done", from_session="s2", payload={"status": "idle"})
    assert "review_subtask" in ActorRunner._format_member_done(ok)


# ---------------------------------------------------------------------------
# _finalize_interrupted_member — converges with stop_member
# ---------------------------------------------------------------------------


def _patch_finalize_deps(
    monkeypatch: pytest.MonkeyPatch,
    *,
    run_status: str,
    plan_nodes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Stub lifecycle's DB seams for _finalize_interrupted_member and record
    every write. Returns the recorder dict."""
    from valuz_agent.modules.tasks import lifecycle as lc_mod

    rec: dict[str, Any] = {"run_updates": [], "events": [], "task_plan": None}

    run = SimpleNamespace(
        session_id="mem-1",
        kind="subtask",
        subtask_key="dev",
        agent_slug="coder",
        dispatched_by="lead-1",
        status=run_status,
        run_dir="/tmp",
        task_id="t1",
        project_id="w1",
    )
    task_row = SimpleNamespace(
        # ``id`` / ``project_id`` are read off the row by ``persist_plan`` (a
        # real TaskRow always carries them) — the fake has to model that.
        id="t-int",
        project_id="w1",
        plan={"subtasks": plan_nodes},
        status="active",
    )

    class _FakeRunDs:
        def __init__(self, _db):
            pass

        async def get_run(self, _sid):
            return run

        async def update_run_by_session(self, **kw):
            rec["run_updates"].append(kw)

    class _FakeTaskDs:
        def __init__(self, _db):
            pass

        async def get_task_by_project(self, _uid, _ws, _tid):
            return task_row

        async def update_task(self, row):
            rec["task_plan"] = row.plan

    class _FakeEventDs:
        def __init__(self, _db):
            pass

        async def append_event(self, *a, **kw):
            rec["events"].append(kw.get("type") or (a[3] if len(a) > 3 else None))

    @asynccontextmanager
    async def _fake_uow(*_a, **_k):
        yield SimpleNamespace()

    async def _fake_emit(*_a, **_k):
        return None

    async def _fake_name(*_a, **_k):
        return "Coder"

    monkeypatch.setattr(lc_mod, "async_unit_of_work", _fake_uow)
    monkeypatch.setattr(lc_mod, "TaskSessionDatastore", _FakeRunDs)
    monkeypatch.setattr(lc_mod, "TaskDatastore", _FakeTaskDs)
    monkeypatch.setattr(lc_mod, "TaskEventDatastore", _FakeEventDs)
    monkeypatch.setattr(lc_mod, "resolve_agent_display_name", _fake_name)
    monkeypatch.setattr(planning, "emit_plan_update", _fake_emit)
    return rec


async def test_finalize_interrupted_member_records_user_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rec = _patch_finalize_deps(
        monkeypatch,
        run_status="active",
        plan_nodes=[{"key": "dev", "title": "dev", "status": "in_progress"}],
    )
    orch = TaskOrchestrator()
    mailbox_registry.register("lead-1")
    try:
        await orch._lifecycle._finalize_interrupted_member(
            session_id="mem-1", task_id="t1", project_id="w1", user_id=LOCAL_USER_ID
        )
        # run → rejected (the stop_member convention, not archived/failed)
        assert rec["run_updates"] and rec["run_updates"][0]["status"] == "rejected"
        # node → rework with a user-stop note
        node = rec["task_plan"]["subtasks"][0]
        assert node["status"] == "rework"
        assert "用户中断" in (node.get("review_feedback") or "")
        # timeline shows a stop, NOT a failure
        assert rec["events"] == ["subtask_stopped"]
        # exactly one member_done(cancelled) reached the lead
        msg = await mailbox_registry.get("lead-1", timeout=0.5)
        assert msg.kind == "member_done"
        assert msg.payload is not None and msg.payload["status"] == "cancelled"
    finally:
        mailbox_registry.unregister("lead-1")


async def test_finalize_interrupted_member_skips_already_recorded_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """stop_member (rejected) / stop_task (paused) already recorded the outcome
    — the loop-exit callback must not overwrite it or double-notify."""
    for parked in ("rejected", "paused"):
        rec = _patch_finalize_deps(
            monkeypatch,
            run_status=parked,
            plan_nodes=[{"key": "dev", "title": "dev", "status": "paused"}],
        )
        orch = TaskOrchestrator()
        mailbox_registry.register("lead-1")
        try:
            await orch._lifecycle._finalize_interrupted_member(
                session_id="mem-1", task_id="t1", project_id="w1", user_id=LOCAL_USER_ID
            )
            assert rec["run_updates"] == []
            assert rec["events"] == []
            assert rec["task_plan"] is None
            with pytest.raises(asyncio.TimeoutError):
                await mailbox_registry.get("lead-1", timeout=0.05)
        finally:
            mailbox_registry.unregister("lead-1")


# ---------------------------------------------------------------------------
# await_member_results — pending liveness + awaiting_user early break
# ---------------------------------------------------------------------------


def _patch_await_deps(monkeypatch: pytest.MonkeyPatch, key_by_session: dict[str, str]) -> None:
    """Same seams as test_actor_v2._patch_await_deps (coordination module)."""
    from valuz_agent.modules.tasks import coordination as coord_mod

    class _FakeRunDs:
        def __init__(self, _db):
            pass

        async def get_run(self, sid):
            sk = key_by_session.get(sid)
            return SimpleNamespace(subtask_key=sk) if sk else None

        async def list_runs(self, _user_id, _task_id):
            return [
                SimpleNamespace(
                    kind="subtask",
                    subtask_key=sk,
                    status="active",
                    session_id=sid,
                    agent_slug="dev",
                )
                for sid, sk in key_by_session.items()
            ]

    class _FakeTaskDs:
        def __init__(self, _db):
            pass

        async def get_task_by_project(self, _user_id, _project_id, _task_id):
            return None

    @asynccontextmanager
    async def _fake_uow(*_a, **_k):
        yield SimpleNamespace()

    monkeypatch.setattr(coord_mod, "async_unit_of_work", _fake_uow)
    monkeypatch.setattr(coord_mod, "TaskSessionDatastore", _FakeRunDs)
    monkeypatch.setattr(coord_mod, "TaskDatastore", _FakeTaskDs)


def _patch_kernel_running(monkeypatch: pytest.MonkeyPatch) -> None:
    from valuz_agent.modules.tasks import coordination as coord_mod

    def _reader():
        async def _get_session(_uid, _sid):
            return SimpleNamespace(status="running", stop_reason=None)

        return SimpleNamespace(get_session=_get_session)

    monkeypatch.setattr(coord_mod, "data_reader", _reader)


async def test_await_timeout_reports_running_member_liveness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A member deep in a long tool call is reported ALIVE, with a hint telling
    the lead not to treat it as dead (the case-B fix)."""
    _patch_await_deps(monkeypatch, {"sB": "B"})
    _patch_kernel_running(monkeypatch)
    from valuz_agent.modules.tasks import coordination as coord_mod

    monkeypatch.setattr(coord_mod, "_HEARTBEAT_S", 0.05)

    async def _no_asks(_user_id):
        return {}

    monkeypatch.setattr(
        coord_mod.CoordinationService, "_pending_asks_by_session", staticmethod(_no_asks)
    )

    orch = TaskOrchestrator()
    lead = "lead-live-1"
    mailbox_registry.register(lead)
    try:
        res = await orch.await_member_results(
            lead_session_id=lead,
            project_id="w1",
            task_id="t1",
            keys=["B"],
            mode="all",
            timeout_s=0.2,
            user_id=LOCAL_USER_ID,
        )
        assert res["timed_out"] is True
        assert res["pending"] == ["B"]
        status = {p["subtask_key"]: p for p in res["pending_status"]}
        assert status["B"]["state"] == "running"
        assert "ALIVE" in res["hint"]
    finally:
        mailbox_registry.unregister(lead)


async def test_await_breaks_early_when_all_pending_awaiting_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every pending member parked on an AskUserQuestion → break out with the
    awaiting_user flag instead of burning the whole timeout."""
    _patch_await_deps(monkeypatch, {"sB": "B"})
    _patch_kernel_running(monkeypatch)
    from valuz_agent.modules.tasks import coordination as coord_mod

    monkeypatch.setattr(coord_mod, "_HEARTBEAT_S", 0.05)

    async def _asks(_user_id):
        return {"sB": "which environment should I deploy to?"}

    monkeypatch.setattr(
        coord_mod.CoordinationService, "_pending_asks_by_session", staticmethod(_asks)
    )

    orch = TaskOrchestrator()
    lead = "lead-ask-1"
    mailbox_registry.register(lead)
    try:
        loop = asyncio.get_running_loop()
        start = loop.time()
        res = await orch.await_member_results(
            lead_session_id=lead,
            project_id="w1",
            task_id="t1",
            keys=["B"],
            mode="all",
            timeout_s=30,  # would block ~30s without the early break
            user_id=LOCAL_USER_ID,
        )
        # The probe runs every ``_PROBE_EVERY_N_SLICES`` heartbeat slices (it
        # answers a question only a human can change), so the break costs that
        # many slices — trivial here because ``_HEARTBEAT_S`` is patched to
        # 0.05s above. The budget below just has to clear it.
        assert loop.time() - start < 2.0
        assert res["awaiting_user"] is True
        assert res["timed_out"] is False
        entry = res["pending_status"][0]
        assert entry["state"] == "awaiting_user"
        assert "deploy" in entry["question"]
        assert "decision inbox" in res["hint"]
    finally:
        mailbox_registry.unregister(lead)


# ---------------------------------------------------------------------------
# finish_task(stopped) — live-member guard
# ---------------------------------------------------------------------------


async def test_finish_task_stopped_rejected_while_members_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from valuz_agent.modules.tasks import lifecycle as lc_mod

    class _FakeRunDs:
        def __init__(self, _db):
            pass

        async def list_runs(self, _user_id, _task_id):
            return [
                SimpleNamespace(kind="subtask", subtask_key="build", status="active"),
            ]

    @asynccontextmanager
    async def _fake_uow(*_a, **_k):
        yield SimpleNamespace()

    monkeypatch.setattr(lc_mod, "async_unit_of_work", _fake_uow)
    monkeypatch.setattr(lc_mod, "TaskSessionDatastore", _FakeRunDs)

    orch = TaskOrchestrator()
    orch._members.add_member("t-guard", "mem-live-1")
    try:
        res = await orch.finish_task(
            task_id="t-guard",
            project_id="w1",
            lead_session_id="lead-g",
            summary="giving up",
            status="stopped",
            user_id=LOCAL_USER_ID,
        )
        assert res["status"] == "rejected"
        assert res["live_subtasks"] == ["build"]
        assert "force=true" in res["error"]
    finally:
        orch._members.discard_member("t-guard", "mem-live-1")


async def test_finish_task_stopped_force_bypasses_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """force=True must get PAST the live-member guard (proven by reaching the
    terminal-write path, stubbed here)."""
    from valuz_agent.modules.tasks import lifecycle as lc_mod

    writes: list[str] = []

    class _FakeTaskDs:
        def __init__(self, _db):
            pass

        async def get_task_by_project(self, *_a):
            # A REAL row. This used to return None, which happened to skip the
            # guards — but ``finish_task`` now rejects a missing task outright
            # (it used to write a terminal event for a task that doesn't
            # exist). An empty plan + no worktree keeps this test on its actual
            # subject: that ``force`` gets past the LIVE-MEMBER guard.
            return SimpleNamespace(plan={}, metadata_={})

        async def update_task_status(self, _uid, _tid, status):
            writes.append(status)

    class _FakeRunDs:
        def __init__(self, _db):
            pass

        async def update_run_by_session(self, **_kw):
            return None

        async def list_runs(self, *_a):
            return []

    class _FakeEventDs:
        def __init__(self, _db):
            pass

        async def append_event(self, *_a, **_kw):
            return None

    @asynccontextmanager
    async def _fake_uow(*_a, **_k):
        yield SimpleNamespace()

    async def _no_session(*_a, **_k):
        return None

    from valuz_agent.modules.tasks import events as events_mod

    monkeypatch.setattr(lc_mod, "async_unit_of_work", _fake_uow)
    monkeypatch.setattr(lc_mod, "TaskDatastore", _FakeTaskDs)
    monkeypatch.setattr(lc_mod, "TaskSessionDatastore", _FakeRunDs)
    monkeypatch.setattr(lc_mod, "TaskEventDatastore", _FakeEventDs)
    # finish_task's terminal write now runs through events.finalize_task —
    # stub the same fakes on its namespace.
    monkeypatch.setattr(events_mod, "TaskDatastore", _FakeTaskDs)
    monkeypatch.setattr(events_mod, "TaskEventDatastore", _FakeEventDs)
    monkeypatch.setattr(lc_mod.kernel_client, "get_session", _no_session)

    orch = TaskOrchestrator()
    orch._members.add_member("t-force", "mem-live-2")
    try:
        res = await orch.finish_task(
            task_id="t-force",
            project_id="w1",
            lead_session_id="lead-f",
            summary="deliberate stop",
            status="stopped",
            force=True,
            user_id=LOCAL_USER_ID,
        )
        assert res == {"ok": True, "status": "stopped"}
        assert writes == ["stopped"]
    finally:
        orch._members.discard_member("t-force", "mem-live-2")
