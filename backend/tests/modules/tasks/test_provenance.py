"""Unit tests for task trigger provenance resolution.

Pins how ``resolve_trigger_provenance`` classifies what spawned a task into
user / chat / agent / automation — the data behind the task-list "由 … 触发"
line. DB fixture mirrors ``test_queries`` (tmp SQLite + monkeypatched
``AsyncSessionLocal``).
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from valuz_agent.infra.auth_context import require_current_user_id
from valuz_agent.infra.database import Base
from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.tasks.models import TaskRow, TaskSessionRow
from valuz_agent.modules.tasks.provenance import resolve_trigger_provenance


@pytest.fixture
def bind_db(tmp_path, monkeypatch):
    import valuz_agent.infra.db as db_mod

    db_file = tmp_path / "prov.db"
    sync_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(sync_engine, tables=[TaskRow.__table__, TaskSessionRow.__table__])
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    monkeypatch.setattr(
        db_mod,
        "AsyncSessionLocal",
        async_sessionmaker(bind=async_engine, expire_on_commit=False),
    )
    return sessionmaker(bind=sync_engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_no_origin_is_user(bind_db):
    async with async_unit_of_work() as db:
        prov = await resolve_trigger_provenance(db, originating_session_id=None)
    assert prov.trigger_type == "user"
    assert prov.trigger_task_id is None and prov.trigger_automation_id is None


@pytest.mark.asyncio
async def test_automation_taken_as_is(bind_db):
    async with async_unit_of_work() as db:
        prov = await resolve_trigger_provenance(
            db,
            originating_session_id=None,
            trigger_type="automation",
            trigger_automation_id="auto-9",
        )
    assert prov.trigger_type == "automation"
    assert prov.trigger_automation_id == "auto-9"


@pytest.mark.asyncio
async def test_automation_invoked_by_agent_also_links_origin_task(bind_db):
    """An agent (in a task) that runs an automation → the spawned task keeps
    type=automation but ALSO records the originating task so it nests under it."""
    uid = require_current_user_id()
    async with async_unit_of_work() as db:
        db.add(
            TaskSessionRow(
                user_id=uid,
                project_id="w1",
                task_id="origin-task",
                session_id="origin-lead",
                agent_slug="行业分析师",
                sequence=0,
                kind="lead",
                status="active",
            )
        )
    async with async_unit_of_work() as db:
        prov = await resolve_trigger_provenance(
            db,
            originating_session_id="origin-lead",
            trigger_type="automation",
            trigger_automation_id="auto-9",
        )
    assert prov.trigger_type == "automation"
    assert prov.trigger_automation_id == "auto-9"
    assert prov.trigger_task_id == "origin-task"  # nests under the originating task
    assert prov.trigger_agent_slug == "行业分析师"


@pytest.mark.asyncio
async def test_conversation_session_is_chat(bind_db):
    # A session with no task-session row is a plain project conversation.
    async with async_unit_of_work() as db:
        prov = await resolve_trigger_provenance(db, originating_session_id="conv-1")
    assert prov.trigger_type == "chat"


@pytest.mark.asyncio
async def test_task_session_is_agent_with_parent_and_agent(bind_db):
    uid = require_current_user_id()
    async with async_unit_of_work() as db:
        db.add(
            TaskSessionRow(
                user_id=uid,
                project_id="w1",
                task_id="parent-task",
                session_id="lead-sess",
                agent_slug="行业分析师",
                sequence=0,
                kind="lead",
                status="active",
            )
        )
    async with async_unit_of_work() as db:
        prov = await resolve_trigger_provenance(db, originating_session_id="lead-sess")
    assert prov.trigger_type == "agent"
    assert prov.trigger_task_id == "parent-task"
    assert prov.trigger_agent_slug == "行业分析师"
