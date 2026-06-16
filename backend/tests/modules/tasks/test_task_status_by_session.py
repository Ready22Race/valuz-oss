"""``TaskSessionDatastore.get_task_status_by_session_ids`` — the session→task
status join the automations activity log uses to surface a task automation's
*live* outcome instead of the frozen kickoff-time run status.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.infra.database import Base
from valuz_agent.modules.tasks.datastore import TaskDatastore, TaskSessionDatastore
from valuz_agent.modules.tasks.models import TaskEventRow, TaskRow, TaskSessionRow


@pytest.fixture
def sessionmaker_(tmp_path):
    db_file = tmp_path / "tasks.db"
    sync_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(
        sync_engine,
        tables=[TaskRow.__table__, TaskEventRow.__table__, TaskSessionRow.__table__],
    )
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    return async_sessionmaker(bind=async_engine, expire_on_commit=False)


def _task(task_id: str, *, status: str) -> TaskRow:
    return TaskRow(
        id=task_id,
        project_id="p1",
        file_path="/x",
        title="T",
        goal="g",
        status=status,
        lead_agent_slug="lead",
        current_holder="lead",
    )


def _run(task_id: str, *, session_id: str) -> TaskSessionRow:
    return TaskSessionRow(
        id=uuid4().hex,
        project_id="p1",
        task_id=task_id,
        session_id=session_id,
        agent_slug="a1",
        sequence=1,
        kind="lead",
    )


async def test_maps_session_ids_to_live_task_status(sessionmaker_) -> None:
    async with sessionmaker_() as db:
        await TaskDatastore(db).create_task("user-A", _task("t-active", status="active"))
        await TaskDatastore(db).create_task("user-A", _task("t-done", status="completed"))
        ds = TaskSessionDatastore(db)
        await ds.create_run("user-A", _run("t-active", session_id="s-active"))
        await ds.create_run("user-A", _run("t-done", session_id="s-done"))

    async with sessionmaker_() as db:
        result = await TaskSessionDatastore(db).get_task_status_by_session_ids(
            "user-A", ["s-active", "s-done", "s-unknown"]
        )

    # Each lead session resolves to its task's live status; unknown ids omitted.
    assert result == {"s-active": "active", "s-done": "completed"}


async def test_scoped_by_owner_and_empty_input(sessionmaker_) -> None:
    async with sessionmaker_() as db:
        await TaskDatastore(db).create_task("user-A", _task("t1", status="completed"))
        await TaskSessionDatastore(db).create_run("user-A", _run("t1", session_id="s1"))

    async with sessionmaker_() as db:
        ds = TaskSessionDatastore(db)
        # Empty input short-circuits without a query.
        assert await ds.get_task_status_by_session_ids("user-A", []) == {}
        # Another owner can't see user-A's task status.
        assert await ds.get_task_status_by_session_ids("user-B", ["s1"]) == {}
        assert await ds.get_task_status_by_session_ids("user-A", ["s1"]) == {"s1": "completed"}
