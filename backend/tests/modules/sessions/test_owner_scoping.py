"""Owner-scoping regression for the sessions layer.

``SessionDatastore`` (attachments) takes the caller's ``user_id`` first on
user-facing reads and stamps it on writes. The ``project_index`` module-level
facade receives the owner explicitly even though it opens its own unit of work.

System paths stay cross-owner: ``project_index.project_of`` (by globally-unique
kernel session id) and the by-id finalize writes
(``update_attachment_parse`` / ``mark_attachments_consumed``).
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.infra.database import Base
from valuz_agent.modules.sessions import project_index
from valuz_agent.modules.sessions.datastore import SessionDatastore
from valuz_agent.modules.sessions.models import ProjectSessionRow, SessionAttachmentRow


@pytest.fixture
def db(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    """tmp SQLite bound to AsyncSessionLocal so both SessionDatastore (via
    async_unit_of_work) and project_index resolve to the same file."""
    import valuz_agent.infra.db as db_mod

    db_file = tmp_path / "sessions.db"
    sync_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(
        sync_engine, tables=[ProjectSessionRow.__table__, SessionAttachmentRow.__table__]
    )
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    maker = async_sessionmaker(bind=async_engine, expire_on_commit=False)
    monkeypatch.setattr(db_mod, "AsyncSessionLocal", maker)
    return maker


def _attachment(session_id: str = "s1") -> SessionAttachmentRow:
    return SessionAttachmentRow(
        session_id=session_id,
        filename="a.txt",
        stored_path="/raw.txt",
        size_bytes=1,
        mime_type="text/plain",
        source_kind="local",
    )


class TestAttachmentOwnerScoping:
    async def test_reads_scoped_by_owner(self, db) -> None:
        async with db() as s:
            row = await SessionDatastore(s).create_attachment("user-A", _attachment())
        async with db() as s:
            ds = SessionDatastore(s)
            assert {r.id for r in await ds.list_attachments("user-A", "s1")} == {row.id}
            assert await ds.list_attachments("user-B", "s1") == []
            assert (await ds.get_attachment("user-A", row.id)) is not None
            assert (await ds.get_attachment("user-B", row.id)) is None

    async def test_delete_owner_scoped(self, db) -> None:
        async with db() as s:
            row = await SessionDatastore(s).create_attachment("user-A", _attachment())
        async with db() as s:
            await SessionDatastore(s).delete_attachment("user-B", row.id)
        async with db() as s:
            assert (await SessionDatastore(s).get_attachment("user-A", row.id)) is not None


class TestProjectIndexOwnerScoping:
    async def test_record_and_reads_scoped_by_owner(self, db) -> None:
        await project_index.record("p1", "sa", kind="chat", user_id="user-A")
        await project_index.record("p1", "sb", kind="chat", user_id="user-B")

        assert await project_index.list_session_ids("p1", user_id="user-A") == ["sa"]
        assert await project_index.count_for_project("p1", user_id="user-A") == 1
        assert {
            r.session_id for r in await project_index.list_recent(user_id="user-A")
        } == {"sa"}
        # project_of is a system lookup by the globally-unique session id.
        assert await project_index.project_of("sb") == "p1"

    async def test_remove_owner_scoped(self, db) -> None:
        for owner, sid in (("user-A", "sa"), ("user-B", "sb")):
            await project_index.record("p1", sid, kind="chat", user_id=owner)
        # user-B's remove_for_project must not touch user-A's row.
        assert await project_index.remove_for_project("p1", user_id="user-B") == ["sb"]
        assert await project_index.list_session_ids("p1", user_id="user-A") == ["sa"]
