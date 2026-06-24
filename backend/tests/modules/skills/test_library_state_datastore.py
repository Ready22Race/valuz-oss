"""SkillDatastore global-library-switch round-trip against real SQLite.

Exercises the ``valuz_skill_index.library_enabled`` column (added by 0007) via
the datastore: default is on (a fresh row is not "disabled"), turning a row off
lists it as disabled by id, the toggle is idempotent, re-enabling clears it, and
an unknown id is a no-op. Per-user isolation is checked too.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.infra.database import Base
from valuz_agent.modules.skills.datastore import SkillDatastore
from valuz_agent.modules.skills.models import SkillIndexRow


def _row(skill_id: str, user_id: str, slug: str) -> SkillIndexRow:
    return SkillIndexRow(
        id=skill_id,
        slug=slug,
        name=slug,
        description="",
        scope="user",
        source="filesystem",
        source_path=f"/tmp/{slug}",
        user_id=user_id,
    )


@pytest.fixture
async def session_ds(tmp_path):  # type: ignore[no-untyped-def]
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'idx.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[SkillIndexRow.__table__])
    session = async_sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield session, SkillDatastore(session)
    finally:
        await session.close()
        await engine.dispose()


async def _seed(session, *rows: SkillIndexRow) -> None:  # type: ignore[no-untyped-def]
    session.add_all(rows)
    await session.commit()


async def test_default_enabled_and_toggle(session_ds) -> None:  # type: ignore[no-untyped-def]
    session, ds = session_ds
    await _seed(session, _row("user:alpha", "u1", "alpha"))

    # Fresh row defaults enabled → not in the disabled set.
    assert await ds.list_library_disabled_ids("u1") == set()

    await ds.set_library_enabled("u1", "user:alpha", False)
    assert await ds.list_library_disabled_ids("u1") == {"user:alpha"}

    # Idempotent.
    await ds.set_library_enabled("u1", "user:alpha", False)
    assert await ds.list_library_disabled_ids("u1") == {"user:alpha"}

    # Re-enable clears it.
    await ds.set_library_enabled("u1", "user:alpha", True)
    assert await ds.list_library_disabled_ids("u1") == set()


async def test_unknown_id_is_noop(session_ds) -> None:  # type: ignore[no-untyped-def]
    _session, ds = session_ds
    await ds.set_library_enabled("u1", "user:ghost", False)  # no such row
    assert await ds.list_library_disabled_ids("u1") == set()


async def test_per_user_isolation(session_ds) -> None:  # type: ignore[no-untyped-def]
    session, ds = session_ds
    await _seed(
        session,
        _row("user:alpha", "u1", "alpha"),
        _row("user:beta", "u2", "beta"),
    )
    await ds.set_library_enabled("u1", "user:alpha", False)
    await ds.set_library_enabled("u2", "user:beta", False)
    assert await ds.list_library_disabled_ids("u1") == {"user:alpha"}
    assert await ds.list_library_disabled_ids("u2") == {"user:beta"}
