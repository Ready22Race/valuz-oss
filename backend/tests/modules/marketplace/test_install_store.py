"""MarketplaceInstallStore — write-only provenance persistence (phase 1).

Covers upsert-on-reinstall (record twice for the same item overwrites, never
duplicates), owner isolation, and cleanup by installed_ref (the skill/agent
delete hook).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from valuz_agent.infra.database import Base
from valuz_agent.modules.marketplace.install_store import (
    MarketplaceInstallRow,
    MarketplaceInstallStore,
)

USER = "user-1"
OTHER = "user-2"


@pytest_asyncio.fixture
async def db() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[MarketplaceInstallRow.__table__])
    session = async_sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()


async def _all_rows(db: AsyncSession) -> list[MarketplaceInstallRow]:
    result = await db.execute(select(MarketplaceInstallRow))
    return list(result.scalars().all())


@pytest.mark.asyncio
async def test_record_inserts_a_new_row(db: AsyncSession) -> None:
    store = MarketplaceInstallStore(db)
    await store.record(
        USER,
        item_id="market:skill:foo",
        item_type="skill",
        installed_ref="foo",
        version="1.0.0",
        source_channel="oss",
        content_hash="abc123",
    )
    await db.commit()

    rows = await _all_rows(db)
    assert len(rows) == 1
    row = rows[0]
    assert row.user_id == USER
    assert row.item_id == "market:skill:foo"
    assert row.item_type == "skill"
    assert row.installed_ref == "foo"
    assert row.version == "1.0.0"
    assert row.content_hash == "abc123"
    assert row.source_channel == "oss"
    assert row.auto_update is False


@pytest.mark.asyncio
async def test_record_upserts_on_reinstall(db: AsyncSession) -> None:
    store = MarketplaceInstallStore(db)
    await store.record(
        USER,
        item_id="market:skill:foo",
        item_type="skill",
        installed_ref="foo",
        version="1.0.0",
        source_channel="oss",
        content_hash="hash-1",
    )
    await store.record(
        USER,
        item_id="market:skill:foo",
        item_type="skill",
        installed_ref="foo-2",
        version="1.1.0",
        source_channel="oss",
        content_hash="hash-2",
    )
    await db.commit()

    rows = await _all_rows(db)
    assert len(rows) == 1
    assert rows[0].version == "1.1.0"
    assert rows[0].installed_ref == "foo-2"
    assert rows[0].content_hash == "hash-2"


@pytest.mark.asyncio
async def test_record_is_scoped_per_owner(db: AsyncSession) -> None:
    store = MarketplaceInstallStore(db)
    await store.record(
        USER,
        item_id="market:skill:foo",
        item_type="skill",
        installed_ref="foo",
        version="1.0.0",
        source_channel="oss",
    )
    await store.record(
        OTHER,
        item_id="market:skill:foo",
        item_type="skill",
        installed_ref="foo",
        version="1.0.0",
        source_channel="oss",
    )
    await db.commit()

    rows = await _all_rows(db)
    assert {r.user_id for r in rows} == {USER, OTHER}
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_remove_by_ref_deletes_only_matching_owner_and_ref(db: AsyncSession) -> None:
    store = MarketplaceInstallStore(db)
    await store.record(
        USER,
        item_id="market:skill:foo",
        item_type="skill",
        installed_ref="foo",
        version="1.0.0",
        source_channel="oss",
    )
    await store.record(
        USER,
        item_id="market:skill:bar",
        item_type="skill",
        installed_ref="bar",
        version="1.0.0",
        source_channel="oss",
    )
    await store.record(
        OTHER,
        item_id="market:skill:foo",
        item_type="skill",
        installed_ref="foo",
        version="1.0.0",
        source_channel="oss",
    )
    await db.commit()

    await store.remove_by_ref(USER, "foo")
    await db.commit()

    rows = await _all_rows(db)
    refs = {(r.user_id, r.installed_ref) for r in rows}
    assert refs == {(USER, "bar"), (OTHER, "foo")}


@pytest.mark.asyncio
async def test_remove_by_ref_is_a_noop_when_nothing_matches(db: AsyncSession) -> None:
    store = MarketplaceInstallStore(db)
    await store.remove_by_ref(USER, "does-not-exist")
    await db.commit()
    assert await _all_rows(db) == []
