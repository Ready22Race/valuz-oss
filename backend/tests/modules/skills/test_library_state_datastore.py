"""SkillDatastore global-library-switch round-trip against real SQLite.

Exercises ``valuz_skill_library_state`` (the table the 0007 migration creates)
through the ORM model + datastore queries: absence = enabled, an explicit off
is listed as disabled, the upsert is idempotent, and toggling back on clears it.
Per-user isolation is checked too (composite PK ``user_id, slug``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.infra.database import Base
from valuz_agent.modules.skills.datastore import SkillDatastore
from valuz_agent.modules.skills.models import SkillLibraryStateRow


@pytest.fixture
async def ds(tmp_path) -> AsyncIterator[SkillDatastore]:  # type: ignore[no-untyped-def]
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'lib.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[SkillLibraryStateRow.__table__])
    session = async_sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield SkillDatastore(session)
    finally:
        await session.close()
        await engine.dispose()


async def test_absence_means_enabled(ds: SkillDatastore) -> None:
    assert await ds.list_library_disabled_slugs("u1") == set()


async def test_disable_then_reenable(ds: SkillDatastore) -> None:
    await ds.set_library_enabled("u1", "alpha", False)
    assert await ds.list_library_disabled_slugs("u1") == {"alpha"}

    # Idempotent upsert — disabling twice keeps a single row / same result.
    await ds.set_library_enabled("u1", "alpha", False)
    assert await ds.list_library_disabled_slugs("u1") == {"alpha"}

    # Re-enabling drops it back out of the disabled set.
    await ds.set_library_enabled("u1", "alpha", True)
    assert await ds.list_library_disabled_slugs("u1") == set()


async def test_per_user_isolation(ds: SkillDatastore) -> None:
    await ds.set_library_enabled("u1", "alpha", False)
    await ds.set_library_enabled("u2", "beta", False)
    assert await ds.list_library_disabled_slugs("u1") == {"alpha"}
    assert await ds.list_library_disabled_slugs("u2") == {"beta"}
