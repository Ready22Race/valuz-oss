"""Shared fixtures for the tasks-module tests.

``db_factory`` had been copy-pasted into eight test modules — the same twenty
lines of engine wiring, drifting apart in which tables each one bothered to
create. It belongs here once.

Why a REAL sqlite file rather than fake datastore classes: the tasks module's
persistence is where its invariants live (the task-status state machine, the
per-task event sequence, owner scoping). A hand-rolled ``_FakeTaskDs`` with
just enough methods to get a test green does not enforce any of them, and it
rots the moment a signature changes — during this refactor two such fakes broke
on unrelated edits and a third had a bug encoded into it as expected behaviour
(``get_task_by_project`` returning None with the comment "no plan guard").
A tmp sqlite file costs milliseconds and can't drift from reality.

Fakes are still right for things that are NOT this module: the kernel client,
the projects/agents datastores, the provider catalogue. Those are seams, and
stubbing a seam is not the same as stubbing yourself.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from valuz_agent.infra.database import Base
from valuz_agent.modules.agents.models import ProjectMemberRow
from valuz_agent.modules.tasks.models import TaskEventRow, TaskRow, TaskSessionRow

# Every task test that touches the DB wants the same three tables. Creating all
# of them unconditionally is cheaper than each module deciding, and removes the
# "this test failed because its fixture forgot a table" class of confusion —
# which is exactly how the eight copies had drifted (one created a single
# table, another two, the rest three).
#
# ``valuz_project_member`` rides along because a real read path reaches it:
# ``queries.list_members`` goes through the resolver seam into
# ``ProjectMemberDatastore``. One extra empty table costs nothing.
_TASK_TABLES = [
    TaskRow.__table__,
    TaskEventRow.__table__,
    TaskSessionRow.__table__,
    ProjectMemberRow.__table__,
]


@pytest.fixture
def db_factory(tmp_path, monkeypatch):
    """A tmp-SQLite async sessionmaker bound into ``infra.db.AsyncSessionLocal``.

    The host is fully async (``async_unit_of_work`` / aiosqlite), so we patch
    ``infra.db.AsyncSessionLocal`` and the code under test binds to this tmp
    engine with no other changes. The returned SYNC sessionmaker is for the
    test's own seed/read helpers — simpler than awaiting in a fixture helper,
    and it reads the same file.
    """
    import valuz_agent.infra.db as db_mod

    db_file = tmp_path / "tasks.db"
    sync_engine = create_engine(
        f"sqlite:///{db_file}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(sync_engine, tables=_TASK_TABLES)

    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    monkeypatch.setattr(
        db_mod,
        "AsyncSessionLocal",
        async_sessionmaker(bind=async_engine, expire_on_commit=False),
    )
    return sessionmaker(bind=sync_engine, expire_on_commit=False)
