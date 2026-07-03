"""Owner-scoping regression tests for ``SkillDatastore``."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.infra.database import Base
from valuz_agent.modules.skills.contracts import SkillManifest
from valuz_agent.modules.skills.datastore import SkillDatastore
from valuz_agent.modules.skills.models import SkillIndexRow
from valuz_agent.modules.skills.service import _upsert_skill_row


@pytest.fixture
def sessionmaker_(tmp_path):
    db_file = tmp_path / "skills.db"
    sync_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(sync_engine, tables=[SkillIndexRow.__table__])
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    return async_sessionmaker(bind=async_engine, expire_on_commit=False)


def _row(skill_id: str, *, slug: str | None = None) -> SkillIndexRow:
    return SkillIndexRow(
        user_id="local-test-owner",
        id=skill_id,
        slug=slug or skill_id,
        name=skill_id,
        scope="user",
        source="valuz",
        source_path=f"/tmp/{skill_id}",
    )


class TestSkillOwnerScoping:
    async def test_create_stamps_owner(self, sessionmaker_) -> None:
        async with sessionmaker_() as db:
            await SkillDatastore(db).create("user-A", _row("s1"))
        async with sessionmaker_() as db:
            row = await SkillDatastore(db).get_by_id("user-A", "s1")
            assert row is not None and row.user_id == "user-A"

    async def test_reads_absent_for_other_owner(self, sessionmaker_) -> None:
        async with sessionmaker_() as db:
            await SkillDatastore(db).create("user-A", _row("s1"))
        async with sessionmaker_() as db:
            ds = SkillDatastore(db)
            assert await ds.get_by_id("user-A", "s1") is not None
            assert await ds.get_by_id("user-B", "s1") is None
            assert {r.id for r in await ds.list_skills("user-A")} == {"s1"}
            assert await ds.list_skills("user-B") == []

    async def test_same_slug_can_exist_for_different_owners(self, sessionmaker_) -> None:
        async with sessionmaker_() as db:
            ds = SkillDatastore(db)
            await ds.create("user-A", _row("official:user-a-skill-creator", slug="skill-creator"))
            await ds.create("user-B", _row("official:user-b-skill-creator", slug="skill-creator"))

        async with sessionmaker_() as db:
            ds = SkillDatastore(db)
            row_a = await ds.get_by_id("user-A", "official:user-a-skill-creator")
            row_b = await ds.get_by_id("user-B", "official:user-b-skill-creator")
            assert row_a is not None and row_a.user_id == "user-A"
            assert row_b is not None and row_b.user_id == "user-B"
            assert row_a.slug == row_b.slug == "skill-creator"

    async def test_same_slug_is_unique_per_owner(self, sessionmaker_) -> None:
        async with sessionmaker_() as db:
            ds = SkillDatastore(db)
            await ds.create("user-A", _row("skill-a", slug="shared"))
            with pytest.raises(IntegrityError):
                await ds.create("user-A", _row("skill-b", slug="shared"))

    async def test_upsert_does_not_copy_manifest_id_into_row_primary_key(
        self, sessionmaker_
    ) -> None:
        manifest = SkillManifest(
            id="official:skill-creator",
            slug="skill-creator",
            name="skill-creator",
            description="test",
            scope="official",
            source="valuz",
            path="/tmp/skill-creator",
        )

        async with sessionmaker_() as db:
            ds = SkillDatastore(db)
            await _upsert_skill_row("user-A", ds, manifest)
            await _upsert_skill_row("user-B", ds, manifest)

        async with sessionmaker_() as db:
            ds = SkillDatastore(db)
            row_a = (await ds.list_skills("user-A"))[0]
            row_b = (await ds.list_skills("user-B"))[0]
            assert row_a.slug == row_b.slug == "skill-creator"
            assert row_a.id != "official:skill-creator"
            assert row_b.id != "official:skill-creator"
            assert row_a.id != row_b.id

    async def test_delete_is_owner_scoped(self, sessionmaker_) -> None:
        async with sessionmaker_() as db:
            await SkillDatastore(db).create("user-A", _row("s1"))
        async with sessionmaker_() as db:
            await SkillDatastore(db).delete("user-B", "s1")
        async with sessionmaker_() as db:
            assert await SkillDatastore(db).get_by_id("user-A", "s1") is not None
