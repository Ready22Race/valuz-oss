"""NotificationService — durable ledger + fan-out (docs/design/notifications.md)."""

# ruff: noqa: I001
from __future__ import annotations

import asyncio

import pytest

import valuz_agent.boot.kernel  # noqa: F401
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from valuz_agent.infra.database import Base
from valuz_agent.modules.notifications.models import NotificationRow
from valuz_agent.modules.notifications.service import NotificationService

OWNER = "local-test-owner"


@pytest.fixture
def db_factory(tmp_path, monkeypatch):
    import valuz_agent.infra.db as db_mod

    db_file = tmp_path / "notif.db"
    sync_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(sync_engine, tables=[NotificationRow.__table__])
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    async_factory = async_sessionmaker(bind=async_engine, expire_on_commit=False)
    monkeypatch.setattr(db_mod, "AsyncSessionLocal", async_factory)
    return sessionmaker(bind=sync_engine, expire_on_commit=False)


def _q(**kw):
    base = dict(
        dedup_key="q:p1",
        kind="question",
        title="architect 需要你确认",
        body="选哪种布局？",
        route="/tasks/t1",
        action="answer",
        task_id="t1",
        pending_id="p1",
    )
    base.update(kw)
    return base


def test_ingest_is_idempotent_by_dedup(db_factory) -> None:
    svc = NotificationService()

    async def run():
        e1 = await svc.ingest(OWNER, **_q())
        e2 = await svc.ingest(OWNER, **_q())  # re-fire — same subject
        entries, unread = await svc.snapshot(OWNER)
        return e1, e2, entries, unread

    e1, e2, entries, unread = asyncio.run(run())
    assert e1 is not None and e2 is not None
    assert e1.id == e2.id  # upsert returned the same row
    assert len(entries) == 1
    assert unread == 1


def test_resolve_clears_from_open_set(db_factory) -> None:
    svc = NotificationService()

    async def run():
        await svc.ingest(OWNER, **_q())
        await svc.resolve(OWNER, "q:p1")
        return await svc.snapshot(OWNER)

    entries, unread = asyncio.run(run())
    assert entries == []
    assert unread == 0


def test_mark_read_drops_unread_but_keeps_open(db_factory) -> None:
    svc = NotificationService()

    async def run():
        e = await svc.ingest(OWNER, **_q())
        await svc.mark_read(OWNER, e.id)
        return await svc.snapshot(OWNER)

    entries, unread = asyncio.run(run())
    assert len(entries) == 1  # still open (unresolved)
    assert entries[0].read_at is not None
    assert unread == 0


def test_owner_scoped(db_factory) -> None:
    svc = NotificationService()

    async def run():
        await svc.ingest(OWNER, **_q())
        await svc.ingest("other", **_q(dedup_key="q:p2", pending_id="p2"))
        mine, _ = await svc.snapshot(OWNER)
        theirs, _ = await svc.snapshot("other")
        return mine, theirs

    mine, theirs = asyncio.run(run())
    assert len(mine) == 1 and len(theirs) == 1
    assert mine[0].pending_id == "p1"


def test_subscribe_receives_snapshot_then_added(db_factory) -> None:
    svc = NotificationService()

    async def run():
        q = await svc.subscribe(OWNER)
        first = await q.get()  # snapshot (empty)
        await svc.ingest(OWNER, **_q())
        second = await q.get()  # added
        await svc.unsubscribe(q)
        return first, second

    first, second = asyncio.run(run())
    assert first.kind == "snapshot"
    assert first.payload["unread"] == 0
    assert second.kind == "added"
    assert second.payload["entry"]["pending_id"] == "p1"


def test_added_not_broadcast_on_refire(db_factory) -> None:
    svc = NotificationService()

    async def run():
        await svc.ingest(OWNER, **_q())
        q = await svc.subscribe(OWNER)
        await q.get()  # snapshot (already has the entry)
        await svc.ingest(OWNER, **_q())  # re-fire — must NOT broadcast added
        # resolve → the only further frame we should see
        await svc.resolve(OWNER, "q:p1")
        frame = await asyncio.wait_for(q.get(), timeout=1.0)
        await svc.unsubscribe(q)
        return frame

    frame = asyncio.run(run())
    assert frame.kind == "resolved"
