"""W1+W2 — WriteThroughStore: dual-write, central-authoritative event seq, idempotency.

Two real SQLAlchemyStores (distinct sqlite files) play local + durable. Asserts:
writes land in both; ``append_event`` is durable-first and the local copy mirrors the
durable's authoritative ``seq``; the pair is idempotent on ``request_id``; reads are
local-first.
"""

# ruff: noqa: I001 — boot.kernel side-effect import MUST precede src.* (sys.path)
from __future__ import annotations

import uuid

import pytest

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect for src.*

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.adapters.sqlalchemy_store.models import Base
from src.adapters.sqlalchemy_store.store import SQLAlchemyStore
from src.adapters.write_through_store import WriteThroughStore
from src.core.agent_config import AgentConfig
from src.core.events import Event
from src.core.types import Message, Session, UserMessage


async def _mk_store(path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return SQLAlchemyStore(async_sessionmaker(engine, expire_on_commit=False)), engine


@pytest.fixture
async def wt(tmp_path):
    local, le = await _mk_store(tmp_path / "local.db")
    durable, de = await _mk_store(tmp_path / "durable.db")
    yield WriteThroughStore(local, durable), local, durable
    await le.dispose()
    await de.dispose()


def _sess(sid: str, owner: str, cwd: str) -> Session:
    return Session(
        id=sid,
        user_id=owner,
        agent_config=AgentConfig(id="a", name="a", model="claude-sonnet-4-6"),
        cwd=cwd,
    )


async def _seed(store, owner, tmp_path):
    sid = uuid.uuid4().hex
    await store.save_session(_sess(sid, owner, str(tmp_path)))
    mid = uuid.uuid4().hex
    await store.save_message(
        owner,
        Message(
            id=mid,
            session_id=sid,
            user_message=UserMessage(text="hi"),
            started_at=0,
            status="running",
        ),
    )
    return sid, mid


async def test_writes_go_to_both(wt, tmp_path):
    store, local, durable = wt
    sid, mid = await _seed(store, "u", tmp_path)
    assert await local.load_session("u", sid) is not None
    assert await durable.load_session("u", sid) is not None
    assert await local.load_message("u", mid) is not None
    assert await durable.load_message("u", mid) is not None


async def test_event_seq_central_and_mirrored(wt, tmp_path):
    store, local, durable = wt
    sid, mid = await _seed(store, "u", tmp_path)
    s1 = await store.append_event("u", sid, mid, Event(type="user_message", data={}))
    s2 = await store.append_event(
        "u", sid, mid, Event(type="assistant_message", data={"text": "x"})
    )
    assert s1 is not None and s2 is not None and s2 > s1
    # both copies hold the SAME seqs (durable-assigned, local-mirrored)
    local_seqs = [e.seq for e in await local.get_events_after("u", sid, after_seq=0)]
    durable_seqs = [e.seq for e in await durable.get_events_after("u", sid, after_seq=0)]
    assert local_seqs == durable_seqs == [s1, s2]


async def test_append_idempotent_across_both(wt, tmp_path):
    store, local, durable = wt
    sid, mid = await _seed(store, "u", tmp_path)
    rid = "rid-1"
    a = await store.append_event("u", sid, mid, Event(type="user_message", data={}), request_id=rid)
    b = await store.append_event("u", sid, mid, Event(type="user_message", data={}), request_id=rid)
    assert a == b
    assert len(await local.get_events("u", sid)) == 1
    assert len(await durable.get_events("u", sid)) == 1


async def test_reads_are_local_first(wt, tmp_path):
    store, local, durable = wt
    sid, mid = await _seed(store, "u", tmp_path)
    # write an event ONLY to durable; local-first reads must not see it
    await durable.append_event("u", sid, mid, Event(type="user_message", data={}))
    assert await store.get_events("u", sid) == []


async def test_sqlalchemy_store_explicit_seq(tmp_path):
    """W2: SQLAlchemyStore stores an explicit (mirror) seq instead of autoincrement."""
    store, engine = await _mk_store(tmp_path / "x.db")
    try:
        sid, mid = await _seed(store, "u", tmp_path)
        got = await store.append_event(
            "u", sid, mid, Event(type="user_message", data={}), request_id="r", seq=4242
        )
        assert got == 4242
        assert [e.seq for e in await store.get_events_after("u", sid, after_seq=0)] == [4242]
    finally:
        await engine.dispose()
