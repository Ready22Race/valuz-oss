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

from src.adapters.durable_outbox import DurableOutbox
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
    """Local-authority (pg-tier) store: read local, durable mirror + outbox."""
    local, le = await _mk_store(tmp_path / "local.db")
    durable, de = await _mk_store(tmp_path / "durable.db")
    store = WriteThroughStore(
        local, durable, authority="local", outbox=DurableOutbox(local._session_factory)
    )
    yield store, local, durable
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


async def test_event_seq_local_authoritative(wt, tmp_path):
    store, local, durable = wt
    sid, mid = await _seed(store, "u", tmp_path)
    s1 = await store.append_event("u", sid, mid, Event(type="user_message", data={}))
    s2 = await store.append_event(
        "u", sid, mid, Event(type="assistant_message", data={"text": "x"})
    )
    # The returned seq is the LOCAL autoincrement (monotonic), and the event
    # lands in BOTH stores. The two stores' seqs are independent — the contract
    # is "the local-first reader gets a consistent monotonic cursor", not that
    # local and durable share seq values.
    assert s1 is not None and s2 is not None and s2 > s1
    local_seqs = [e.seq for e in await local.get_events_after("u", sid, after_seq=0)]
    assert local_seqs == [s1, s2]  # returned seq == local read cursor
    assert len(await durable.get_events("u", sid)) == 2  # durable also has both


async def test_local_preexisting_ids_never_drop_events(tmp_path):
    """Regression: durable seq must NOT be forced onto the local PK.

    Reproduces the data-loss bug — when the LOCAL store already holds events at
    ids that overlap the durable's (independent, lower) autoincrement, forcing
    ``local.id = durable_seq`` collided and silently dropped every mirrored
    event. Local is now seq-authoritative, so its autoincrement never collides.
    """
    local, le = await _mk_store(tmp_path / "local.db")
    durable, de = await _mk_store(tmp_path / "durable.db")
    try:
        sid, mid = await _seed(local, "u", tmp_path)  # seed DIRECTLY on local
        # Pre-fill local with 5 events → local autoincrement now at 5.
        for _ in range(5):
            await local.append_event("u", sid, mid, Event(type="thinking", data={}))
        assert len(await local.get_events("u", sid)) == 5
        # Durable is fresh (its autoincrement starts at 1) — the exact overlap
        # that used to collide. Append through the write-through store.
        store = WriteThroughStore(
            local, durable, authority="local", outbox=DurableOutbox(local._session_factory)
        )
        s = await store.append_event("u", sid, mid, Event(type="user_message", data={}))
        # The new event is in BOTH stores — none dropped.
        assert s is not None and s > 5  # local autoincrement continued
        assert len(await local.get_events("u", sid)) == 6
        assert len(await durable.get_events("u", sid)) == 1
    finally:
        await le.dispose()
        await de.dispose()


async def test_append_idempotent_across_both(wt, tmp_path):
    store, local, durable = wt
    sid, mid = await _seed(store, "u", tmp_path)
    rid = "rid-1"
    a = await store.append_event("u", sid, mid, Event(type="user_message", data={}), request_id=rid)
    b = await store.append_event("u", sid, mid, Event(type="user_message", data={}), request_id=rid)
    assert a == b
    assert len(await local.get_events("u", sid)) == 1
    assert len(await durable.get_events("u", sid)) == 1


async def test_durable_authority_reads_from_durable(tmp_path):
    """remote tier: durable is the system of record — reads + seq come from it,
    and the local buffer is never the read source (the ephemeral-sandbox case)."""
    local, le = await _mk_store(tmp_path / "local.db")
    durable, de = await _mk_store(tmp_path / "durable.db")
    try:
        store = WriteThroughStore(local, durable, authority="durable")
        sid, mid = await _seed(store, "u", tmp_path)
        s = await store.append_event("u", sid, mid, Event(type="user_message", data={}))
        # Returned seq is the DURABLE seq; the durable holds the event…
        durable_seqs = [e.seq for e in await durable.get_events_after("u", sid, after_seq=0)]
        assert durable_seqs == [s]
        # …and the store reads from durable (buffer also got it, best-effort).
        assert len(await store.get_events("u", sid)) == 1
        assert len(await local.get_events("u", sid)) == 1  # buffer copy

        # Prove the READ source is durable: write an event ONLY to durable;
        # the store must surface it even though local never saw it.
        await durable.append_event("u", sid, mid, Event(type="assistant_message", data={}))
        assert len(await store.get_events("u", sid)) == 2
        assert len(await local.get_events("u", sid)) == 1
    finally:
        await le.dispose()
        await de.dispose()


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
