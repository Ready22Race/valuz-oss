"""Tests for the ``propose_agent`` / ``list_skills`` natural-language agent
creation tools (``integrations/tools_agent_proposal.py``).

Uses the ``monkeypatch.setattr(db_mod, "AsyncSessionLocal", ...)`` pattern so
the handlers' internal ``async_unit_of_work()`` binds to an in-memory SQLite
seeded with the skill index + connector tables.
"""

from __future__ import annotations

import json

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import valuz_agent.boot.kernel  # noqa: F401 — surfaces ``src.*`` on sys.path

from src.core.tools import ExecContext

from valuz_agent.infra.database import Base
from valuz_agent.integrations import tools_agent_proposal as tap
from valuz_agent.integrations.tools_agent_proposal import (
    _list_agents_handler,
    _list_project_members_handler,
    _list_skills_handler,
    _propose_agent_handler,
)
from valuz_agent.modules.agents.models import AgentRow, ProjectMemberRow
from valuz_agent.modules.connectors.models import (
    ConnectorAttrRow,
    ConnectorOAuthRow,
    ConnectorRow,
)
from valuz_agent.modules.skills.models import SkillIndexRow

USER_ID = "local-test-owner"


@pytest_asyncio.fixture
async def seeded_db(monkeypatch):
    """In-memory async SQLite with the skill index + connector tables, bound
    into ``async_unit_of_work`` via ``AsyncSessionLocal``."""
    import valuz_agent.infra.db as db_mod

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(
                c,
                tables=[
                    SkillIndexRow.__table__,
                    ConnectorRow.__table__,
                    ConnectorAttrRow.__table__,
                    ConnectorOAuthRow.__table__,
                    AgentRow.__table__,
                    ProjectMemberRow.__table__,
                ],
            )
        )
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(db_mod, "AsyncSessionLocal", factory)

    # Seed one indexed skill + one connector owned by the test owner.
    async with factory() as db:
        db.add(
            SkillIndexRow(
                slug="market-research",
                name="Market Research",
                description="Research helper",
                scope="user",
                source="filesystem",
                source_path="/tmp/skills/market-research",
                user_id=USER_ID,
            )
        )
        db.add(
            ConnectorRow(
                slug="github",
                display_name="GitHub",
                connector_type="custom",
                transport="http",
                url="https://example.com/mcp",
                user_id=USER_ID,
            )
        )
        db.add(
            AgentRow(
                slug="research-helper",
                name="Research Helper",
                description="An existing library agent",
                source="custom",
                user_id=USER_ID,
            )
        )
        await db.commit()
    yield factory
    await engine.dispose()


def _ctx() -> ExecContext:
    return ExecContext(session_id="sess-1")


async def test_propose_minimal_ok(seeded_db) -> None:
    res = await _propose_agent_handler(
        {"name": "Helper", "instructions": "Be helpful."}, _ctx()
    )
    assert not res.is_error
    payload = json.loads(res.content)
    assert payload["ok"] is True
    assert payload["spec"]["name"] == "Helper"
    assert payload["spec"]["runtime"] == "claude_agent"
    assert payload["warnings"] == []


async def test_missing_required_fields(seeded_db) -> None:
    res = await _propose_agent_handler({"name": "", "instructions": "x"}, _ctx())
    assert res.is_error
    assert "name" in res.content


async def test_invalid_runtime(seeded_db) -> None:
    res = await _propose_agent_handler(
        {"name": "H", "instructions": "i", "runtime": "bogus"}, _ctx()
    )
    assert res.is_error
    assert "runtime" in res.content


async def test_unindexed_skill_rejected(seeded_db) -> None:
    res = await _propose_agent_handler(
        {"name": "H", "instructions": "i", "skills": ["does-not-exist"]}, _ctx()
    )
    assert res.is_error
    assert "does-not-exist" in res.content


async def test_indexed_skill_ok(seeded_db) -> None:
    res = await _propose_agent_handler(
        {"name": "H", "instructions": "i", "skills": ["market-research"]}, _ctx()
    )
    assert not res.is_error
    payload = json.loads(res.content)
    assert payload["spec"]["skills"] == ["market-research"]


async def test_missing_connector_warns_not_fails(seeded_db) -> None:
    res = await _propose_agent_handler(
        {
            "name": "H",
            "instructions": "i",
            "connectors": ["github", "ghost-connector"],
        },
        _ctx(),
    )
    assert not res.is_error
    payload = json.loads(res.content)
    assert payload["ok"] is True
    # Existing connector binds; the missing one is surfaced as a warning.
    assert payload["spec"]["connectors"] == ["github", "ghost-connector"]
    assert any("ghost-connector" in w for w in payload["warnings"])


async def test_list_skills_returns_indexed(seeded_db) -> None:
    res = await _list_skills_handler({}, _ctx())
    assert not res.is_error
    payload = json.loads(res.content)
    slugs = {s["slug"] for s in payload["skills"]}
    assert "market-research" in slugs


@pytest.mark.parametrize("effort", ["low", "max"])
async def test_valid_effort_ok(seeded_db, effort) -> None:
    res = await _propose_agent_handler(
        {"name": "H", "instructions": "i", "effort": effort}, _ctx()
    )
    assert not res.is_error
    assert json.loads(res.content)["spec"]["effort"] == effort


async def test_list_agents_returns_library(seeded_db) -> None:
    res = await _list_agents_handler({}, _ctx())
    assert not res.is_error
    slugs = {a["slug"] for a in json.loads(res.content)["agents"]}
    assert "research-helper" in slugs


async def test_list_members_requires_project(seeded_db, monkeypatch) -> None:
    async def _no_project(_sid):
        return None

    monkeypatch.setattr(tap, "_resolve_project_id", _no_project)
    res = await _list_project_members_handler({}, _ctx())
    assert res.is_error
    assert "no project" in res.content


async def test_list_members_with_project(seeded_db, monkeypatch) -> None:
    async def _project(_sid):
        return "p1"

    monkeypatch.setattr(tap, "_resolve_project_id", _project)
    # Seed a member referencing the existing library agent.
    import valuz_agent.infra.db as db_mod

    async with db_mod.AsyncSessionLocal() as db:
        db.add(
            ProjectMemberRow(
                project_id="p1",
                agent_slug="research-helper-1",
                source_agent_slug="research-helper",
                user_id=USER_ID,
            )
        )
        await db.commit()

    res = await _list_project_members_handler({}, _ctx())
    assert not res.is_error
    payload = json.loads(res.content)
    assert payload["project_id"] == "p1"
    assert payload["members"][0]["source_agent_slug"] == "research-helper"
    assert payload["members"][0]["name"] == "Research Helper"


async def test_deploy_requires_project(seeded_db, monkeypatch) -> None:
    from valuz_agent.integrations.tools_agent_proposal import _deploy_agent_handler

    async def _no_project(_sid):
        return None

    monkeypatch.setattr(tap, "_resolve_project_id", _no_project)
    res = await _deploy_agent_handler({"agent_slug": "research-helper"}, _ctx())
    assert res.is_error
    assert "no project" in res.content
