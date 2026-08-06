"""What happens to a task when nobody is minding it.

Three defects that shared one shape — a task whose actor is gone, or whose
project is gone, kept looking fine to everything that checks:

* an inbox with no reader read as a live lead, so the watchdog never blocked
  the task and ``inject_into_task`` reported delivery into a queue nobody drains;
* deleting a project left its tasks behind, and the next boot resurrected them
  against deleted kernel sessions and announced them as blocked;
* a member report lost its mailbox delivery whenever the timeline write that
  shared its unit of work failed.
"""

from __future__ import annotations

import asyncio

from valuz_agent.modules.tasks.mailbox import MailboxRegistry
from valuz_agent.modules.tasks.models import TaskEventRow, TaskRow, TaskSessionRow
from valuz_agent.modules.tasks.plan import TaskPlan
from valuz_agent.modules.tasks.purge import purge_project_tasks, purge_tasks

OWNER = "local-test-owner"


def _seed(db_factory, *, task_id: str, project_id: str = "p1", status: str = "active") -> None:
    plan = TaskPlan()
    plan.add([{"key": "a", "title": "A", "goal": "g", "agent": "worker"}])
    db = db_factory()
    try:
        db.add(
            TaskRow(
                id=task_id,
                user_id=OWNER,
                project_id=project_id,
                file_path=f"tasks/{task_id}.md",
                title="t",
                goal="g",
                status=status,
                lead_agent_slug="lead",
                current_holder="lead",
                plan=plan.to_dict(),
            )
        )
        db.add(
            TaskSessionRow(
                id=f"run-{task_id}",
                user_id=OWNER,
                project_id=project_id,
                task_id=task_id,
                session_id=f"lead-{task_id}",
                agent_slug="lead",
                sequence=1,
                kind="lead",
                status="active",
            )
        )
        db.add(
            TaskEventRow(
                id=f"ev-{task_id}",
                user_id=OWNER,
                project_id=project_id,
                task_id=task_id,
                sequence=1,
                type="task_created",
                actor="user",
                payload={},
            )
        )
        db.commit()
    finally:
        db.close()


def _counts(db_factory, task_id: str) -> tuple[int, int, int]:
    db = db_factory()
    try:
        return (
            len([r for r in db.query(TaskRow).all() if r.id == task_id]),
            len([r for r in db.query(TaskSessionRow).all() if r.task_id == task_id]),
            len([r for r in db.query(TaskEventRow).all() if r.task_id == task_id]),
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# The liveness oracle
# ---------------------------------------------------------------------------


def test_a_box_with_no_reader_is_not_a_live_session() -> None:
    """``register`` is non-owning, so "a box exists" cannot mean "someone reads it".

    This is the whole bug in one assertion: three subsystems asked
    ``is_registered`` whether the lead loop was alive, and a box pre-seeded for
    a loop that never started answered yes for the life of the process.
    """
    reg = MailboxRegistry()
    reg.register("s1")

    assert reg.is_registered("s1") is True
    assert reg.is_owned("s1") is False, (
        "a pre-seeded box has no reader — reporting it as live blinds the "
        "watchdog that exists to catch exactly this task"
    )

    token = reg.claim("s1")
    assert reg.is_owned("s1") is True
    reg.release("s1", token)
    assert reg.is_owned("s1") is False


def test_an_unclaimed_box_stays_unowned_forever() -> None:
    """Nothing reclaims it — which is fine, as long as nobody calls it alive.

    ``release`` cannot drop a box that was never claimed (the token guard sees
    no claim and returns), and ``unregister`` has no production caller. The fix
    is not to reclaim the box but to stop reading its existence as liveness.
    """
    reg = MailboxRegistry()
    reg.register("s1")
    reg.release("s1", 12345)  # a stale token from some other loop

    assert reg.is_registered("s1") is True, "still unreclaimed — by design"
    assert reg.is_owned("s1") is False, "and still, correctly, not alive"


# ---------------------------------------------------------------------------
# Purge
# ---------------------------------------------------------------------------


def test_purge_removes_the_task_and_everything_indexed_under_it(db_factory) -> None:
    _seed(db_factory, task_id="t-purge")
    assert _counts(db_factory, "t-purge") == (1, 1, 1)

    assert asyncio.run(purge_tasks(OWNER, ["t-purge"])) == 1

    assert _counts(db_factory, "t-purge") == (0, 0, 0), (
        "runs and events have no foreign key to the header — leaving them "
        "behind makes invisible garbage that nothing else will ever collect"
    )


def test_purge_is_idempotent_and_owner_scoped(db_factory) -> None:
    _seed(db_factory, task_id="t-mine")
    db = db_factory()
    try:
        row = db.get(TaskRow, "t-mine")
        db.add(
            TaskRow(
                id="t-theirs",
                user_id="somebody-else",
                project_id=row.project_id,
                file_path="tasks/t-theirs.md",
                title="t",
                goal="g",
                status="active",
                lead_agent_slug="lead",
                current_holder="lead",
                plan={},
            )
        )
        db.commit()
    finally:
        db.close()

    assert asyncio.run(purge_tasks(OWNER, ["t-mine", "t-theirs"])) == 1
    assert asyncio.run(purge_tasks(OWNER, ["t-mine"])) == 0

    db = db_factory()
    try:
        assert db.get(TaskRow, "t-theirs") is not None, "another owner's task is not ours to delete"
    finally:
        db.close()


def test_project_deletion_actually_calls_the_cascade(db_factory) -> None:
    """The wiring, not just the door.

    `purge_project_tasks` existing proves nothing on its own — the bug was that
    `delete_project` never called anything like it.
    """
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.infra.eventbus import EventBus
    from valuz_agent.modules.projects.datastore import ProjectDatastore
    from valuz_agent.modules.projects.models import ProjectRow
    from valuz_agent.modules.projects.service import ProjectService

    _seed(db_factory, task_id="t-cascade", project_id="doomed")
    db = db_factory()
    try:
        db.add(
            ProjectRow(
                id="doomed",
                user_id=OWNER,
                name="Doomed",
                kind="project",
                root_path="/tmp/doomed",
            )
        )
        db.commit()
    finally:
        db.close()

    async def _delete() -> None:
        async with async_unit_of_work() as db:
            await ProjectService(
                datastore=ProjectDatastore(db), event_bus=EventBus()
            ).delete_project(OWNER, "doomed")

    asyncio.run(_delete())

    assert _counts(db_factory, "t-cascade") == (0, 0, 0), (
        "an active task surviving its project gets respawned by the next boot "
        "against a deleted kernel session and announced as blocked"
    )


def test_deleting_a_project_takes_its_tasks_with_it(db_factory) -> None:
    """The cascade that was missing.

    Left behind, an ``active`` task with no kernel sessions gets respawned by
    the next boot against a dead session id and announced as blocked — for a
    project the user deleted, on a row with no delete path.
    """
    _seed(db_factory, task_id="t-a", project_id="doomed")
    _seed(db_factory, task_id="t-b", project_id="doomed")
    _seed(db_factory, task_id="t-keep", project_id="other")

    assert asyncio.run(purge_project_tasks(OWNER, "doomed")) == 2

    assert _counts(db_factory, "t-a") == (0, 0, 0)
    assert _counts(db_factory, "t-b") == (0, 0, 0)
    assert _counts(db_factory, "t-keep") == (1, 1, 1)
