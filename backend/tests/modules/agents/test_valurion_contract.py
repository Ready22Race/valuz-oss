"""Valurion system-Agent contract.

These tests intentionally exercise the service boundary: every HTTP, bootstrap,
channel, automation, and project path ultimately relies on these invariants.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.infra.database import Base
from valuz_agent.modules.agents.datastore import AgentDatastore
from valuz_agent.modules.agents.models import AgentRow
from valuz_agent.modules.agents.service import (
    AgentManagedFieldError,
    AgentNotDeletableError,
    AgentService,
)

OWNER = "owner-valurion"


@pytest.fixture
async def db(tmp_path) -> AsyncIterator:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'valurion.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[AgentRow.__table__])
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()


async def test_ensure_builtin_agent_is_idempotent_and_repairs_managed_fields(db) -> None:
    svc = AgentService(db)  # type: ignore[arg-type]

    first = await svc.ensure_builtin_agent(OWNER)
    first_id = first.id

    # Simulate system-field drift from a previous build. User-selectable brain
    # settings remain untouched by the repair.
    first.kind = "standard"
    first.source = "custom"
    first.resource_policy = "explicit"
    first.inherit_global_instructions = False
    first.permission_mode = "plan"
    first.deletable = True
    first.runtime = "codex"
    first.model = "gpt-5"
    first.effort = "high"
    await db.commit()

    repaired = await svc.ensure_builtin_agent(OWNER)
    rows = await AgentDatastore(db).list_agents(OWNER)

    assert repaired.id == first_id
    assert len([row for row in rows if row.slug == "valurion"]) == 1
    assert repaired.slug == "valurion"
    assert repaired.kind == "system"
    assert repaired.source == "builtin"
    assert repaired.resource_policy == "all_available"
    assert repaired.inherit_global_instructions is True
    assert repaired.permission_mode == "full_access"
    assert repaired.deletable is False
    assert repaired.runtime == "codex"
    assert repaired.model == "gpt-5"
    assert repaired.effort == "high"


async def test_system_agent_managed_fields_and_resources_cannot_be_edited_or_deleted(db) -> None:
    svc = AgentService(db)  # type: ignore[arg-type]
    await svc.ensure_builtin_agent(OWNER)

    with pytest.raises(AgentManagedFieldError):
        await svc.update_agent(OWNER, "valurion", {"skills": ["secret-skill"]})
    with pytest.raises(AgentManagedFieldError):
        await svc.update_agent(OWNER, "valurion", {"instructions": "replace product prompt"})
    with pytest.raises(AgentNotDeletableError):
        await svc.delete_agent(OWNER, "valurion", cascade=True)


async def test_new_standard_agent_defaults_to_inheriting_prompt_with_empty_resources(db) -> None:
    row = await AgentService(db).create_agent(  # type: ignore[arg-type]
        OWNER,
        {"name": "Researcher"},
    )

    assert row.kind == "standard"
    assert row.source == "custom"
    assert row.resource_policy == "explicit"
    assert row.inherit_global_instructions is True
    assert row.skills == []
    assert row.connector_types == []
    assert row.knowledge_scope == []
    assert row.deletable is True


async def test_copy_valurion_copies_brain_but_not_identity_prompt_or_resources(db) -> None:
    svc = AgentService(db)  # type: ignore[arg-type]
    valurion = await svc.ensure_builtin_agent(OWNER)
    valurion.runtime = "codex"
    valurion.model = "gpt-5"
    valurion.effort = "xhigh"
    await db.commit()

    copied = await svc.copy_agent(OWNER, "valurion")

    assert copied.slug != "valurion"
    assert copied.name == "Valurion Copy"
    assert copied.kind == "standard"
    assert copied.source == "user"
    assert copied.deletable is True
    assert copied.resource_policy == "explicit"
    assert copied.inherit_global_instructions is True
    assert copied.instructions == ""
    assert copied.runtime == "codex"
    assert copied.model == "gpt-5"
    assert copied.effort == "xhigh"
    assert copied.provider_id is None
    assert copied.skills == []
    assert copied.connector_types == []
    assert copied.knowledge_scope == []


async def test_copy_standard_agent_deep_copies_portable_configuration(db) -> None:
    svc = AgentService(db)  # type: ignore[arg-type]
    original = await svc.create_agent(
        OWNER,
        {
            "name": "Analyst",
            "description": "Own description",
            "instructions": "Own instructions",
            "runtime": "deepagents",
            "model": "gpt-5",
            "provider_id": "provider-1",
            "effort": "max",
            "skills": ["xlsx"],
            "connector_types": ["search"],
            "knowledge_scope": ["kb-1"],
            "permission_mode": "plan",
            "inherit_global_instructions": False,
            "avatar": "chart",
        },
    )

    copied = await svc.copy_agent(OWNER, original.slug)

    assert copied.id != original.id
    assert copied.slug != original.slug
    assert copied.source == "user"
    assert copied.kind == "standard"
    assert copied.instructions == original.instructions
    assert copied.inherit_global_instructions is False
    assert copied.runtime == original.runtime
    assert copied.model == original.model
    assert copied.provider_id == original.provider_id
    assert copied.effort == original.effort
    assert copied.skills == original.skills
    assert copied.connector_types == original.connector_types
    assert copied.knowledge_scope == original.knowledge_scope
    assert copied.permission_mode == original.permission_mode
    assert copied.avatar == original.avatar
    assert copied.skills is not original.skills
    assert copied.connector_types is not original.connector_types
    assert copied.knowledge_scope is not original.knowledge_scope


async def test_legacy_helper_alias_resolves_to_canonical_valurion(db) -> None:
    svc = AgentService(db)  # type: ignore[arg-type]
    canonical = await svc.ensure_builtin_agent(OWNER)

    assert (await svc.get_agent(OWNER, "valuz-helper")).id == canonical.id
