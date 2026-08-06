"""TaskHealthMonitor watchdog tests (task attention & reliability, P2).

Drives ``sweep_once`` directly against a tmp-SQLite fixture. Liveness is the
lead's mailbox registration; the monitor only acts after ``confirm_sweeps``
consecutive dead-looking passes, flipping the task ``active → blocked`` and
emitting ``task_blocked(reason="lead_dead")``.
"""

# ruff: noqa: I001
from __future__ import annotations

import asyncio

import pytest

import valuz_agent.boot.kernel  # noqa: F401
from sqlalchemy import select

from valuz_agent.modules.tasks.recovery import (
    TaskHealthConfig,
    TaskHealthMonitor,
)
from valuz_agent.modules.tasks.mailbox import mailbox_registry
from valuz_agent.modules.tasks.models import TaskEventRow, TaskRow, TaskSessionRow

OWNER = "local-test-owner"




@pytest.fixture(autouse=True)
def _reset_mailbox():
    mailbox_registry._boxes.clear()
    yield
    mailbox_registry._boxes.clear()


def _seed(db_factory, *, task_id="t1", status="active", lead_session_id="lead-s") -> None:
    db = db_factory()
    try:
        db.add(
            TaskRow(
                user_id=OWNER,
                id=task_id,
                project_id="w1",
                file_path="/tmp/t.md",
                title="T",
                goal="g",
                status=status,
                created_by="user",
                lead_agent_slug="lead",
                current_holder="lead",
                plan={"subtasks": []},
            )
        )
        if lead_session_id is not None:
            db.add(
                TaskSessionRow(
                    user_id=OWNER,
                    project_id="w1",
                    task_id=task_id,
                    session_id=lead_session_id,
                    agent_slug="lead",
                    sequence=0,
                    kind="lead",
                    status="active",
                )
            )
        db.commit()
    finally:
        db.close()


def _task_status(db_factory, task_id="t1") -> str:
    db = db_factory()
    try:
        return db.execute(select(TaskRow.status).filter_by(id=task_id)).scalar_one()
    finally:
        db.close()


def _event_types(db_factory, task_id="t1") -> list[str]:
    db = db_factory()
    try:
        return [
            e.type
            for e in db.execute(
                select(TaskEventRow).filter_by(task_id=task_id).order_by(TaskEventRow.sequence)
            )
            .scalars()
            .all()
        ]
    finally:
        db.close()


def _monitor() -> TaskHealthMonitor:
    # confirm_sweeps=2 default; startup_delay irrelevant (we call sweep_once).
    return TaskHealthMonitor(TaskHealthConfig())


def test_live_lead_loop_is_healthy(db_factory) -> None:
    _seed(db_factory)
    mailbox_registry.register("lead-s")  # loop alive
    mon = _monitor()
    acted = asyncio.run(mon.sweep_once())
    assert acted == []
    assert _task_status(db_factory) == "active"


def test_dead_lead_needs_two_sweeps_before_blocking(db_factory) -> None:
    _seed(db_factory)
    # No mailbox registration → loop absent.
    mon = _monitor()
    # First sweep: suspected, not yet acted.
    assert asyncio.run(mon.sweep_once()) == []
    assert _task_status(db_factory) == "active"
    # Second consecutive sweep: confirmed → blocked.
    assert asyncio.run(mon.sweep_once()) == ["t1"]
    assert _task_status(db_factory) == "blocked"
    assert "task_blocked" in _event_types(db_factory)


def test_recovered_lead_clears_suspicion(db_factory) -> None:
    _seed(db_factory)
    mon = _monitor()
    assert asyncio.run(mon.sweep_once()) == []  # suspected once
    mailbox_registry.register("lead-s")  # a resume landed — loop back
    assert asyncio.run(mon.sweep_once()) == []  # suspicion cleared
    assert _task_status(db_factory) == "active"
    # A later death restarts the 2-sweep count from scratch.
    mailbox_registry.unregister("lead-s")
    assert asyncio.run(mon.sweep_once()) == []
    assert asyncio.run(mon.sweep_once()) == ["t1"]
    assert _task_status(db_factory) == "blocked"


def test_blocked_event_payload_reason_is_lead_dead(db_factory) -> None:
    _seed(db_factory)
    mon = _monitor()
    asyncio.run(mon.sweep_once())
    asyncio.run(mon.sweep_once())
    db = db_factory()
    try:
        ev = (
            db.execute(
                select(TaskEventRow).filter_by(task_id="t1", type="task_blocked")
            )
            .scalars()
            .one()
        )
        assert ev.payload["reason"] == "lead_dead"
    finally:
        db.close()


def test_task_with_no_lead_run_is_left_alone(db_factory) -> None:
    _seed(db_factory, lead_session_id=None)
    mon = _monitor()
    assert asyncio.run(mon.sweep_once()) == []
    assert asyncio.run(mon.sweep_once()) == []
    assert _task_status(db_factory) == "active"


def test_only_active_tasks_are_swept(db_factory) -> None:
    _seed(db_factory, task_id="paused-1", status="paused")
    mon = _monitor()
    asyncio.run(mon.sweep_once())
    asyncio.run(mon.sweep_once())
    assert _task_status(db_factory, "paused-1") == "paused"


def test_disabled_when_interval_zero() -> None:
    from datetime import timedelta

    cfg = TaskHealthConfig(interval=timedelta(seconds=0))
    assert cfg.enabled is False


def test_active_lead_bindings_is_one_query_and_skips_rejected_leads(db_factory) -> None:
    """The sweep runs every 60s forever, so it reads four columns in ONE query
    instead of a full-row scan plus a list_runs per task — and it must make the
    same pick as ``pick_lead_run``: a commit-race loser's rejected lead row
    never wins over the live one."""
    import asyncio

    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.modules.tasks.datastore import TaskDatastore
    from valuz_agent.modules.tasks.models import TaskRow, TaskSessionRow

    db = db_factory()
    try:
        db.add(
            TaskRow(
                id="t-live",
                user_id=OWNER,
                project_id="w1",
                file_path="tasks/t-live.md",
                title="live",
                goal="g",
                status="active",
                lead_agent_slug="lead",
                current_holder="lead",
            )
        )
        db.add(
            TaskRow(
                id="t-done",
                user_id=OWNER,
                project_id="w1",
                file_path="tasks/t-done.md",
                title="done",
                goal="g",
                status="completed",
                lead_agent_slug="lead",
                current_holder="lead",
            )
        )
        # A commit-race loser sits alongside the winner, inserted FIRST.
        for rid, sid, status in (
            ("r-loser", "loser-sess", "rejected"),
            ("r-winner", "winner-sess", "active"),
        ):
            db.add(
                TaskSessionRow(
                    id=rid,
                    user_id=OWNER,
                    project_id="w1",
                    task_id="t-live",
                    session_id=sid,
                    agent_slug="lead",
                    sequence=0,
                    kind="lead",
                    status=status,
                )
            )
        db.commit()
    finally:
        db.close()

    async def _run() -> list[tuple]:
        async with async_unit_of_work(commit=False) as adb:
            return await TaskDatastore(adb).list_active_lead_bindings()

    bindings = asyncio.run(_run())
    assert [b[0] for b in bindings] == ["t-live"], "only ACTIVE tasks are swept"
    assert bindings[0][1:] == (OWNER, "w1", "winner-sess"), (
        "a rejected lead row must never be handed to the watchdog — it would "
        "flip a healthy task to blocked via a mailbox that never registers"
    )
