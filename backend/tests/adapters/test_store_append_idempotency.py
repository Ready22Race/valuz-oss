"""Phase B-F — ``SQLAlchemyStore.append_event`` idempotency via ``request_id``.

A retried REMOTE append reuses one ``request_id``; the unique
``(user_id, event_uid)`` index turns the duplicate into a conflict and the
store returns the ORIGINAL ``seq`` (never a second row). Local appends
(``request_id=None``) keep inserting — NULL event_uid is distinct under the
index. The key is scoped per owner.
"""

# ruff: noqa: I001 — boot.kernel side-effect import MUST precede src.* (sys.path)
from __future__ import annotations

import uuid

import pytest

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect for src.*

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.adapters.sqlalchemy_store.models import Base
from src.adapters.sqlalchemy_store.store import SQLAlchemyStore
from src.core.agent_config import AgentConfig
from src.core.events import Event
from src.core.types import Message, Session, UserMessage


@pytest.fixture
async def store(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'kernel.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield SQLAlchemyStore(async_sessionmaker(engine, expire_on_commit=False))
    await engine.dispose()


async def _seed(store, owner: str, tmp_path) -> tuple[str, str]:
    sess = Session(
        id=uuid.uuid4().hex,
        user_id=owner,
        agent_config=AgentConfig(id="a", name="a", model="claude-sonnet-4-6"),
        cwd=str(tmp_path),
    )
    await store.save_session(sess)
    msg = Message(
        id=uuid.uuid4().hex,
        session_id=sess.id,
        user_message=UserMessage(text="hi"),
        started_at=0,
        status="running",
    )
    await store.save_message(owner, msg)
    return sess.id, msg.id


async def test_append_without_request_id_inserts_each_time(store, tmp_path):
    sid, mid = await _seed(store, "u", tmp_path)
    s1 = await store.append_event("u", sid, mid, Event(type="user_message", data={}))
    s2 = await store.append_event("u", sid, mid, Event(type="user_message", data={}))
    assert s1 != s2  # distinct rows — local behaviour unchanged
    assert len(await store.get_events("u", sid)) == 2


async def test_append_same_request_id_is_idempotent(store, tmp_path):
    sid, mid = await _seed(store, "u", tmp_path)
    rid = "req-123"
    s1 = await store.append_event(
        "u", sid, mid, Event(type="user_message", data={}), request_id=rid
    )
    # Retry with the SAME request_id (even a different payload) → no second row.
    s2 = await store.append_event(
        "u", sid, mid, Event(type="tool_use", data={"x": 1}), request_id=rid
    )
    assert s1 == s2  # original seq returned
    assert len(await store.get_events("u", sid)) == 1  # only the first persisted


async def test_request_id_scoped_per_owner(store, tmp_path):
    # The unique key is (user_id, event_uid): the same request_id under two
    # different owners must NOT collide.
    sid_a, mid_a = await _seed(store, "ua", tmp_path)
    sid_b, mid_b = await _seed(store, "ub", tmp_path)
    a = await store.append_event(
        "ua", sid_a, mid_a, Event(type="user_message", data={}), request_id="same"
    )
    b = await store.append_event(
        "ub", sid_b, mid_b, Event(type="user_message", data={}), request_id="same"
    )
    assert a is not None and b is not None and a != b
