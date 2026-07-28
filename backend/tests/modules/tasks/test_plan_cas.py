"""persist_plan is a CAS write door — version bumps + conflict re-apply.

Two defects this pins against regression:

* Node-status writes (dispatch → in_review → done …) used to leave
  ``plan_version`` frozen, so every ``task_plan_update`` snapshot after the
  last structural edit carried the same version — and the frontend plan-card
  feed, which dedups on ``plan_version``, silently discarded all of them.
* The plan column is a whole-document JSON write. Without the version
  predicate, two concurrent read-modify-write cycles (lead loop vs heartbeat
  vs user stop) both started from the same snapshot and the loser's node
  mutations were reverted by the winner's stale copy.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.tasks import planning
from valuz_agent.modules.tasks.datastore import TaskDatastore, TaskEventDatastore
from valuz_agent.modules.tasks.models import TaskEventRow, TaskRow
from valuz_agent.modules.tasks.plan import TaskPlan

OWNER = "local-test-owner"


def _seed_task(db_factory, task_id: str = "t-cas") -> None:
    plan = TaskPlan()
    plan.add(
        [
            {"key": "a", "title": "A", "goal": "ga", "agent": "worker"},
            {"key": "b", "title": "B", "goal": "gb", "agent": "worker"},
        ]
    )
    db = db_factory()
    try:
        db.add(
            TaskRow(
                id=task_id,
                user_id=OWNER,
                project_id="w1",
                file_path=f"tasks/{task_id}.md",
                title="cas",
                goal="g",
                status="active",
                lead_agent_slug="lead",
                current_holder="lead",
                plan=plan.to_dict(),
                plan_version=3,
            )
        )
        db.commit()
    finally:
        db.close()


def test_node_status_writes_bump_plan_version(db_factory) -> None:
    """Every persist_plan write moves the version — the feed's dedup key.

    mark_node_dispatched / mark_in_review are pure node-status writes (no
    structural edit); their snapshots must still carry fresh versions or the
    plan-card feed drops them.
    """
    _seed_task(db_factory)

    async def _run() -> None:
        await planning.mark_node_dispatched(
            project_id="w1",
            task_id="t-cas",
            subtask_key="a",
            agent="worker",
            session_id="m1",
            user_id=OWNER,
        )
        await planning.mark_in_review(
            task_id="t-cas", project_id="w1", member_session_id="m1", user_id=OWNER
        )

    # mark_in_review resolves the member's run row; seed it via the real store
    db = db_factory()
    try:
        from valuz_agent.modules.tasks.models import TaskSessionRow

        db.add(
            TaskSessionRow(
                id="r1",
                user_id=OWNER,
                project_id="w1",
                task_id="t-cas",
                session_id="m1",
                agent_slug="worker",
                sequence=1,
                kind="subtask",
                subtask_key="a",
                status="active",
            )
        )
        db.commit()
    finally:
        db.close()

    asyncio.run(_run())

    db = db_factory()
    try:
        row = db.get(TaskRow, "t-cas")
        assert row.plan_version == 5, "two node-status writes → version 3 → 5"
        versions = [
            e.payload["plan_version"]
            for e in db.execute(
                select(TaskEventRow).order_by(TaskEventRow.sequence)
            ).scalars()
            if e.type == "task_plan_update"
        ]
        assert versions == [4, 5], (
            "snapshots must carry strictly increasing versions — equal versions "
            f"are dropped by the plan-card feed dedup (got {versions})"
        )
    finally:
        db.close()


def test_cas_conflict_reapplies_mutation_on_fresh_plan(db_factory) -> None:
    """A writer holding a stale row retries and composes with the winner.

    Simulates the real interleaving: writer A loads the task, then writer B
    commits a node change (bumping the version) before A persists. A's CAS
    must fail, reload, re-apply its own mutation, and land WITHOUT reverting
    B's node.
    """
    _seed_task(db_factory, task_id="t-race")

    async def _run() -> None:
        async with async_unit_of_work() as db_a:
            task_ds_a = TaskDatastore(db_a)
            event_ds_a = TaskEventDatastore(db_a)
            stale_row = await task_ds_a.get_task(OWNER, "t-race")

            # Writer B lands first, on its own unit of work.
            async with async_unit_of_work() as db_b:
                task_ds_b = TaskDatastore(db_b)
                row_b = await task_ds_b.get_task(OWNER, "t-race")

                def _b(p: TaskPlan) -> bool:
                    p.update_node("b", status="in_progress")
                    return True

                assert (
                    await planning.persist_plan(
                        task_ds_b,
                        event_ds_a,
                        row_b,
                        mutate=_b,
                        actor="system",
                        session_id=None,
                        user_id=OWNER,
                    )
                    is not None
                )

            # Writer A persists off its stale row — must retry, not revert B.
            def _a(p: TaskPlan) -> bool:
                p.update_node("a", status="in_progress")
                return True

            persisted = await planning.persist_plan(
                task_ds_a,
                event_ds_a,
                stale_row,
                mutate=_a,
                actor="system",
                session_id=None,
                user_id=OWNER,
            )
            assert persisted is not None

    asyncio.run(_run())

    db = db_factory()
    try:
        row = db.get(TaskRow, "t-race")
        statuses = {n["key"]: n["status"] for n in row.plan["subtasks"]}
        assert statuses == {"a": "in_progress", "b": "in_progress"}, (
            "the CAS retry must compose both writers' node mutations — "
            f"a last-writer-wins revert leaves one 'planned' (got {statuses})"
        )
        assert row.plan_version == 5, "two writes → 3 → 5"
    finally:
        db.close()
