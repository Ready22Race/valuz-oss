"""Per-tier write-through policy — best-effort (pg) vs strict (remote).

Best-effort: a durable outage NEVER blocks the local-first write; the failed op
is queued in the DurableOutbox and re-pushed on recovery (idempotent replay).
Strict: a durable failure is fail-loud. Two real SQLAlchemyStores (distinct
SQLite files) play local + durable; a toggleable wrapper simulates the outage.
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


class FlakyDurable:
    """Wraps a real store; ``fail=True`` makes every WRITE raise (outage sim)."""

    def __init__(self, inner: SQLAlchemyStore) -> None:
        self._inner = inner
        self.fail = False

    def _guard(self) -> None:
        if self.fail:
            raise RuntimeError("durable down")

    async def save_session(self, session):
        self._guard()
        return await self._inner.save_session(session)

    async def save_message(self, user_id, message):
        self._guard()
        return await self._inner.save_message(user_id, message)

    async def delete_session(self, user_id, session_id):
        self._guard()
        return await self._inner.delete_session(user_id, session_id)

    async def append_event(
        self, user_id, session_id, message_id, event, *, request_id=None, seq=None
    ):
        self._guard()
        return await self._inner.append_event(
            user_id, session_id, message_id, event, request_id=request_id, seq=seq
        )

    def __getattr__(self, name):  # reads pass straight through
        return getattr(self._inner, name)


async def _mk_store(path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return SQLAlchemyStore(async_sessionmaker(engine, expire_on_commit=False)), engine


def _sess(sid, owner, cwd):
    return Session(
        id=sid,
        user_id=owner,
        agent_config=AgentConfig(id="a", name="a", model="claude-sonnet-4-6"),
        cwd=cwd,
    )


async def _seed(store, owner, cwd):
    sid = uuid.uuid4().hex
    await store.save_session(_sess(sid, owner, cwd))
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


@pytest.fixture
async def be(tmp_path):
    local, le = await _mk_store(tmp_path / "local.db")
    durable_inner, de = await _mk_store(tmp_path / "durable.db")
    durable = FlakyDurable(durable_inner)
    # The outbox lives in the LOCAL db (same engine/session factory).
    outbox = DurableOutbox(local._session_factory)  # type: ignore[attr-defined]
    store = WriteThroughStore(local, durable, durable_required=False, outbox=outbox)
    yield store, local, durable, durable_inner, outbox, str(tmp_path)
    await le.dispose()
    await de.dispose()


async def test_durable_outage_does_not_block_local_write(be):
    store, local, durable, durable_inner, outbox, cwd = be
    durable.fail = True
    sid, mid = await _seed(store, "u", cwd)
    seq = await store.append_event("u", sid, mid, Event(type="user_message", data={}))

    # Local write succeeded despite the durable outage.
    assert seq is not None
    assert await local.load_session("u", sid) is not None
    assert len(await local.get_events("u", sid)) == 1
    # Durable has nothing yet; the ops are queued (session, message, event = 3).
    assert await durable_inner.load_session("u", sid) is None
    assert await outbox.pending_count() == 3


async def test_drain_re_pushes_on_recovery(be):
    store, local, durable, durable_inner, outbox, cwd = be
    durable.fail = True
    sid, mid = await _seed(store, "u", cwd)
    await store.append_event("u", sid, mid, Event(type="user_message", data={}))

    # Durable recovers; the drainer flushes the backlog in order.
    durable.fail = False
    drained = await store.drain_outbox()
    assert drained == 3
    assert await outbox.pending_count() == 0
    assert await durable_inner.load_session("u", sid) is not None
    assert await durable_inner.load_message("u", mid) is not None
    # The event reached both stores. Seqs are per-store/independent (durable
    # autoincrements on replay) — the contract is "both hold the event", not
    # equal seq values.
    assert len(await local.get_events("u", sid)) == 1
    assert len(await durable_inner.get_events("u", sid)) == 1


async def test_drain_idempotent_replay(be):
    store, local, durable, durable_inner, outbox, cwd = be
    # First write while UP (no outbox), then a duplicate-driving outage write.
    sid, mid = await _seed(store, "u", cwd)
    await store.append_event("u", sid, mid, Event(type="user_message", data={}), request_id="r1")
    durable.fail = True
    # Re-append the SAME event (same request_id) during the outage → queued.
    await store.append_event("u", sid, mid, Event(type="user_message", data={}), request_id="r1")
    durable.fail = False
    await store.drain_outbox()
    # event_uid idempotency: durable still holds exactly one event for r1.
    assert len(await durable_inner.get_events("u", sid)) == 1


async def test_partial_drain_stops_at_first_failure(be):
    store, local, durable, durable_inner, outbox, cwd = be
    durable.fail = True
    sid, mid = await _seed(store, "u", cwd)  # queues 2 ops (session, message)
    durable.fail = False
    # Drain after re-failing on the SECOND op: only the first is drained, the
    # rest stay queued (ordering preserved). Simulate by failing mid-drain.
    calls = {"n": 0}
    real_save_message = durable_inner.save_message

    async def _flaky_save_message(*a, **k):
        calls["n"] += 1
        raise RuntimeError("still flaky on message")

    durable_inner.save_message = _flaky_save_message  # type: ignore[method-assign]
    drained = await store.drain_outbox()
    assert drained == 1  # session re-pushed, message replay failed → stop
    assert await outbox.pending_count() == 1
    durable_inner.save_message = real_save_message  # type: ignore[method-assign]
    assert await store.drain_outbox() == 1
    assert await outbox.pending_count() == 0


async def test_strict_mode_is_fail_loud(tmp_path):
    local, le = await _mk_store(tmp_path / "l.db")
    durable_inner, de = await _mk_store(tmp_path / "d.db")
    durable = FlakyDurable(durable_inner)
    try:
        strict = WriteThroughStore(local, durable, durable_required=True)
        durable.fail = True
        with pytest.raises(RuntimeError, match="durable down"):
            await strict.save_session(_sess(uuid.uuid4().hex, "u", str(tmp_path)))
    finally:
        await le.dispose()
        await de.dispose()


def test_best_effort_requires_outbox(tmp_path):
    with pytest.raises(ValueError, match="requires a DurableOutbox"):
        WriteThroughStore(object(), object(), durable_required=False)  # type: ignore[arg-type]
