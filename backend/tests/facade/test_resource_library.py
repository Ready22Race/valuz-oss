"""Tests for ``valuz_agent.facade.resources.ResourceLibrary``.

Agent kind: full round-trip (save → get → list) against a real in-memory SQLite,
using the ``monkeypatch.setattr(db_mod, "AsyncSessionLocal", ...)`` pattern so
``async_unit_of_work`` binds to the test DB.

Skill / connector / kb kinds: list-smoke only (returns [] on empty DB) — the
real services are heavy (filesystem scans, parser setup, secret store) so we
just verify the facade wiring doesn't blow up rather than doing a full
end-to-end with all the moving parts.
"""

from __future__ import annotations

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from valuz_agent.facade.resources import ResourceLibrary, ResourceRef, ResourceSnapshot
from valuz_agent.infra.database import Base
from valuz_agent.modules.agents.models import AgentRow, ProjectMemberRow

# ---------------------------------------------------------------------------
# DB fixture — monkeypatches AsyncSessionLocal so async_unit_of_work uses it
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def agent_db(monkeypatch):
    """In-memory async SQLite seeded with agent + member tables.

    Monkeypatches ``infra.db.AsyncSessionLocal`` so every
    ``async_unit_of_work()`` call inside ``ResourceLibrary`` binds to this
    test database.
    """
    import valuz_agent.infra.db as db_mod

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(
                c, tables=[AgentRow.__table__, ProjectMemberRow.__table__]
            )
        )

    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(db_mod, "AsyncSessionLocal", session_factory)
    yield session_factory
    await engine.dispose()


# ---------------------------------------------------------------------------
# Helper — agent snapshot factory
# ---------------------------------------------------------------------------

USER_ID = "local-test-owner"


def _agent_snapshot(slug: str = "test-agent", name: str = "Test Agent") -> ResourceSnapshot:
    return ResourceSnapshot(
        kind="agent",
        key=slug,
        name=name,
        data={
            "slug": slug,
            "name": name,
            "description": "A test agent",
            "instructions": "Do stuff.",
            "runtime": "claude_agent",
            "model": "claude-sonnet-4-6",
            "skills": [],
            "connector_types": [],
            "provider_id": None,
            "effort": None,
            "avatar": None,
        },
    )


# ---------------------------------------------------------------------------
# Agent round-trip tests
# ---------------------------------------------------------------------------


class TestAgentRoundTrip:
    async def test_save_then_get_returns_matching_snapshot(self, agent_db) -> None:
        lib = ResourceLibrary()
        snap = _agent_snapshot("my-agent", "My Agent")
        ref = await lib.save(USER_ID, snap)

        assert ref.kind == "agent"
        assert ref.key == "my-agent"
        assert ref.name == "My Agent"

        retrieved = await lib.get(USER_ID, "agent", "my-agent")
        assert retrieved is not None
        assert retrieved.kind == "agent"
        assert retrieved.key == "my-agent"
        assert retrieved.name == "My Agent"
        assert retrieved.data["instructions"] == "Do stuff."

    async def test_list_includes_saved_agent(self, agent_db) -> None:
        lib = ResourceLibrary()
        await lib.save(USER_ID, _agent_snapshot("list-me", "List Me"))

        refs = await lib.list(USER_ID, "agent")
        keys = [r.key for r in refs]
        assert "list-me" in keys

    async def test_get_missing_agent_returns_none(self, agent_db) -> None:
        lib = ResourceLibrary()
        result = await lib.get(USER_ID, "agent", "does-not-exist")
        assert result is None

    async def test_save_existing_slug_updates_in_place(self, agent_db) -> None:
        lib = ResourceLibrary()
        await lib.save(USER_ID, _agent_snapshot("upd-agent", "Original"))

        updated_snap = _agent_snapshot("upd-agent", "Updated")
        updated_snap.data["instructions"] = "Updated instructions."
        ref2 = await lib.save(USER_ID, updated_snap)
        assert ref2.key == "upd-agent"

        retrieved = await lib.get(USER_ID, "agent", "upd-agent")
        assert retrieved is not None
        assert retrieved.data["instructions"] == "Updated instructions."

    async def test_list_returns_empty_for_different_user(self, agent_db) -> None:
        lib = ResourceLibrary()
        await lib.save(USER_ID, _agent_snapshot("private-agent"))

        refs = await lib.list("other-user", "agent")
        assert all(r.key != "private-agent" for r in refs)


# ---------------------------------------------------------------------------
# Skill list smoke
# ---------------------------------------------------------------------------


class TestSkillListSmoke:
    async def test_list_skill_returns_list(self, monkeypatch) -> None:
        """list("skill") should return a list (possibly empty) without crashing."""
        # Patch get_skill_service to return a stub that yields a minimal service
        from valuz_agent.modules.skills.models import SkillsCatalog

        class _FakeSkillService:
            async def list_catalog(self, project_id: str, **_: object) -> SkillsCatalog:
                return SkillsCatalog(project_id=project_id, skills=[])

        async def _fake_get_skill_service():  # type: ignore[return]
            yield _FakeSkillService()

        monkeypatch.setattr("valuz_agent.api.deps.get_skill_service", _fake_get_skill_service)

        lib = ResourceLibrary()
        refs = await lib.list(USER_ID, "skill")
        assert isinstance(refs, list)

    async def test_list_skill_maps_skills_to_refs(self, monkeypatch) -> None:
        from valuz_agent.modules.skills.models import SkillsCatalog, SkillView

        fake_view = SkillView(
            id="skill-1",
            name="My Skill",
            description="desc",
            scope="user",
            source="user",
            path="/fake/path",
            slug="my-skill",
            enabled=True,
        )

        class _FakeSkillService:
            async def list_catalog(self, project_id: str, **_: object) -> SkillsCatalog:
                return SkillsCatalog(project_id=project_id, skills=[fake_view])

        async def _fake_get_skill_service():  # type: ignore[return]
            yield _FakeSkillService()

        monkeypatch.setattr("valuz_agent.api.deps.get_skill_service", _fake_get_skill_service)

        lib = ResourceLibrary()
        refs = await lib.list(USER_ID, "skill")
        assert len(refs) == 1
        assert refs[0] == ResourceRef(kind="skill", key="my-skill", name="My Skill")


# ---------------------------------------------------------------------------
# Connector list smoke
# ---------------------------------------------------------------------------


class TestConnectorListSmoke:
    async def test_list_connector_returns_list(self, tmp_path, monkeypatch) -> None:
        """list("connector") should return a list without crashing on empty DB."""
        import valuz_agent.infra.db as db_mod

        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        from valuz_agent.modules.connectors.models import ConnectorAttrRow, ConnectorRow

        async with engine.begin() as conn:
            await conn.run_sync(
                lambda c: Base.metadata.create_all(
                    c, tables=[ConnectorRow.__table__, ConnectorAttrRow.__table__]
                )
            )

        monkeypatch.setattr(
            db_mod, "AsyncSessionLocal", async_sessionmaker(bind=engine, expire_on_commit=False)
        )
        # Patch secret store path to avoid touching real keychain. ``secrets_dir``
        # is a computed property (= data_dir / "secrets") — patch the field it
        # derives from, not the property itself.
        from valuz_agent.infra.config import settings

        monkeypatch.setattr(settings, "data_dir", tmp_path)

        lib = ResourceLibrary()
        refs = await lib.list(USER_ID, "connector")
        assert isinstance(refs, list)


# ---------------------------------------------------------------------------
# KB list smoke
# ---------------------------------------------------------------------------


class TestKbListSmoke:
    async def test_list_kb_returns_list(self, monkeypatch) -> None:
        """list("kb") should return a list without crashing."""

        class _FakeDocService:
            async def list_kbs(self):
                return []

        async def _fake_get_document_service():  # type: ignore[return]
            yield _FakeDocService()

        monkeypatch.setattr("valuz_agent.api.deps.get_document_service", _fake_get_document_service)

        lib = ResourceLibrary()
        refs = await lib.list(USER_ID, "kb")
        assert isinstance(refs, list)
        assert refs == []
