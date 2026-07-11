"""Resource-deletion hooks clean up ``marketplace_install`` provenance.

Real DB (in-memory sqlite), real ``MarketplaceInstallStore``, real
``AgentService`` / ``SkillDatastore`` — verifies the delete-hooks wired into
``modules/agents/service.py`` and ``modules/skills/datastore.py`` +
``modules/skills/service.py`` actually remove the provenance row, so a later
reinstall of the same item re-establishes fresh state instead of looking
"already tracked" against a stale row.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from valuz_agent.infra.database import Base
from valuz_agent.modules.agents.datastore import AgentDatastore
from valuz_agent.modules.agents.models import AgentRow, ProjectMemberRow
from valuz_agent.modules.agents.service import AgentService
from valuz_agent.modules.marketplace.install_store import (
    MarketplaceInstallRow,
    MarketplaceInstallStore,
)
from valuz_agent.modules.projects.models import ProjectRow
from valuz_agent.modules.skills.datastore import SkillDatastore
from valuz_agent.modules.skills.models import SkillIndexRow

USER = "local-test-owner"


@pytest_asyncio.fixture
async def db() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(
            Base.metadata.create_all,
            tables=[
                AgentRow.__table__,
                ProjectMemberRow.__table__,
                ProjectRow.__table__,
                SkillIndexRow.__table__,
                MarketplaceInstallRow.__table__,
            ],
        )
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()


async def _installs_for(db: AsyncSession, user_id: str) -> list[MarketplaceInstallRow]:
    result = await db.execute(
        select(MarketplaceInstallRow).where(MarketplaceInstallRow.user_id == user_id)
    )
    return list(result.scalars().all())


@pytest.mark.asyncio
async def test_delete_agent_removes_marketplace_install_row(db: AsyncSession) -> None:
    agents = AgentDatastore(db)
    await agents.create(
        USER,
        AgentRow(user_id=USER, slug="mkt-meeting-notes", name="Meeting Notes", source="custom"),
    )
    await MarketplaceInstallStore(db).record(
        USER,
        item_id="market:agent:meeting-notes",
        item_type="agent_template",
        installed_ref="mkt-meeting-notes",
        version="1.0.0",
        source_channel="oss",
    )
    await db.commit()
    assert len(await _installs_for(db, USER)) == 1

    svc = AgentService(db, connector_service=None)
    await svc.delete_agent(USER, "mkt-meeting-notes")
    await db.commit()

    assert await _installs_for(db, USER) == []


@pytest.mark.asyncio
async def test_delete_agent_cleanup_is_best_effort_when_table_missing() -> None:
    # A narrow-schema engine (no marketplace_install table) must not make the
    # delete itself fail — the cleanup hook swallows the storage error.
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(
            Base.metadata.create_all,
            tables=[AgentRow.__table__, ProjectMemberRow.__table__, ProjectRow.__table__],
        )
    session = async_sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        agents = AgentDatastore(session)
        await agents.create(
            USER, AgentRow(user_id=USER, slug="no-provenance", name="X", source="custom")
        )
        await session.commit()

        svc = AgentService(session, connector_service=None)
        await svc.delete_agent(USER, "no-provenance")  # must not raise
        await session.commit()

        assert await agents.get_agent(USER, "no-provenance") is None
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_skill_datastore_session_cleanup_removes_marketplace_install_row(
    db: AsyncSession,
) -> None:
    ds = SkillDatastore(db)
    await MarketplaceInstallStore(db).record(
        USER,
        item_id="market:skill:fresh",
        item_type="skill",
        installed_ref="fresh",
        version="1.0.0",
        source_channel="oss",
    )
    await db.commit()
    assert len(await _installs_for(db, USER)) == 1

    # Exercises the exact hook installed in SkillLibraryService.delete_skill —
    # a real session-backed store, not the full filesystem delete pipeline
    # (covered separately in tests/modules/skills/test_service.py).
    await MarketplaceInstallStore(ds.session).remove_by_ref(USER, "fresh")
    await db.commit()

    assert await _installs_for(db, USER) == []
