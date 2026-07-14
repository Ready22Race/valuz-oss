"""User-level control-plane stream — the always-on multiplexed lifecycle SSE.

Covers ``iter_user_events_sse`` / ``list_user_events_after`` in
``event_sse_adapter``: lifecycle-only projection (text-free), per-frame
``session_id``, cursor advance + dedup, heartbeat, and the §9.2 no-DB-hold
invariant (each poll is a discrete read, nothing held between ticks).
"""

# ruff: noqa: I001 — boot.kernel side-effect import MUST precede src.* (sys.path)
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect for src.*

from valuz_agent.adapters import event_sse_adapter as adapter
from valuz_agent.adapters.data_reader import bind_data_reader


def _ev(seq: int, session_id: str, type_: str, **data):
    return SimpleNamespace(seq=seq, session_id=session_id, type=type_, data=data, timestamp=seq)


def _live_ev(seq: int, session_id: str, type_: str, **data):
    """An ``EventData``-shaped live frame from the owner cross-session tap."""
    return SimpleNamespace(seq=seq, session_id=session_id, type=type_, data=data, timestamp=seq)


class FakeReader:
    """Minimal DataReader honouring ``after_seq`` + ``types``; counts calls."""

    def __init__(self, events):
        self._events = events
        self.calls = 0
        self.last_types = None

    async def get_events_after_for_user(self, user_id, *, after_seq=0, types=None, limit=200):
        self.calls += 1
        self.last_types = types
        rows = [e for e in self._events if e.seq > after_seq and (types is None or e.type in types)]
        return rows[:limit]


@pytest.fixture
def bind_reader():
    bound = {}

    def _bind(events):
        reader = FakeReader(events)
        bound["reader"] = reader
        bind_data_reader(reader)
        return reader

    yield _bind
    bind_data_reader(None)


@pytest.fixture
def live_tap(monkeypatch):
    """Patch the owner cross-session live tap; returns a setter for live events.

    Defaults to an empty tap (the stream then relies on the durable backfill),
    so ``iter_user_events_sse`` never touches a real kernel in tests.
    """
    events: list = []

    async def _fake(user_id):
        for e in events:
            yield e

    monkeypatch.setattr(adapter.kernel_client, "subscribe_all_events_for", _fake)

    def _set(evs):
        events.clear()
        events.extend(evs)

    return _set


class TestListUserEventsAfter:
    async def test_translates_lifecycle_text_free_with_session_id(self, bind_reader):
        reader = bind_reader(
            [
                _ev(1, "sess-1", "user_message", message="secret prompt text"),
                _ev(2, "sess-1", "session_idle", stop_reason="end_turn"),
                _ev(3, "sess-2", "user_message", message="another"),
                _ev(4, "sess-2", "session_error", message="boom", category="provider"),
                _ev(5, "sess-1", "session_update", status="running"),
            ]
        )
        frames = await adapter.list_user_events_after("user-A")

        assert reader.last_types == adapter.CONTROL_LIFECYCLE_TYPES
        by_seq = {f.seq: f for f in frames}
        assert by_seq[1].event_type == "run.started"
        assert by_seq[1].session_id == "sess-1"
        # Text-free: the prompt never rides the control plane.
        assert "secret prompt text" not in json.dumps(by_seq[1].payload)
        assert by_seq[2].event_type == "run.finished"
        assert by_seq[2].payload["status"] == "idle"
        assert by_seq[4].event_type == "run.finished"
        assert by_seq[4].payload["status"] == "failed"
        assert by_seq[5].event_type == "run.status"
        assert by_seq[5].payload["status"] == "running"

    async def test_after_seq_cursor(self, bind_reader):
        bind_reader(
            [
                _ev(1, "s", "user_message"),
                _ev(2, "s", "session_idle"),
                _ev(3, "s", "user_message"),
            ]
        )
        frames = await adapter.list_user_events_after("user-A", after_seq=2)
        assert [f.seq for f in frames] == [3]


class TestIterUserEventsSse:
    async def test_backfill_frames_then_advance_cursor(self, bind_reader, live_tap):
        bind_reader([_ev(1, "s", "user_message"), _ev(2, "s", "session_idle")])
        gen = adapter.iter_user_events_sse("user-A", after_seq=0)
        try:
            first = await anext(gen)
            second = await anext(gen)
        finally:
            await gen.aclose()

        assert first["event"] == "run.started"
        assert second["event"] == "run.finished"
        payload = json.loads(second["data"])
        assert payload["seq"] == 2
        assert payload["session_id"] == "s"

    async def test_live_tap_emits_lifecycle_and_dedups(self, bind_reader, live_tap):
        # The live cross-session tap is the primary path; a lifecycle frame it
        # delivers is emitted, text-free, and a frame at/under the cursor is
        # deduped against the backfill.
        bind_reader([])  # nothing to backfill
        live_tap(
            [
                _live_ev(0, "s1", "user_message", message="secret"),  # seq 0 ≤ cursor → deduped
                _live_ev(7, "s1", "session_idle", stop_reason="end_turn"),
            ]
        )
        gen = adapter.iter_user_events_sse("user-A", after_seq=0)
        try:
            frame = await anext(gen)
        finally:
            await gen.aclose()
        assert frame["event"] == "run.finished"
        payload = json.loads(frame["data"])
        assert payload["seq"] == 7
        assert payload["session_id"] == "s1"

    async def test_live_tap_drops_non_lifecycle(self, bind_reader, live_tap):
        bind_reader([])
        live_tap(
            [
                _live_ev(3, "s1", "text_delta", text="tok"),  # not lifecycle → dropped
                _live_ev(4, "s1", "session_error", message="boom"),
            ]
        )
        gen = adapter.iter_user_events_sse("user-A", after_seq=0)
        try:
            frame = await anext(gen)
        finally:
            await gen.aclose()
        assert frame["event"] == "run.finished"
        assert json.loads(frame["data"])["seq"] == 4

    async def test_disconnect_predicate_stops_the_loop(self, bind_reader, live_tap):
        bind_reader([])  # nothing to emit
        gen = adapter.iter_user_events_sse("user-A", is_disconnected=lambda: True)
        with pytest.raises(StopAsyncIteration):
            await anext(gen)

    async def test_no_db_session_held_between_reads(self, bind_reader, live_tap):
        # §9.2: reads are DISCRETE. Idle no longer polls per-second — the
        # initial backfill is one read; the floor is throttled and the live tap
        # (empty here) carries the rest. Exactly one read produced the frame.
        reader = bind_reader([_ev(1, "s", "user_message")])
        gen = adapter.iter_user_events_sse("user-A", after_seq=0)
        try:
            await anext(gen)
        finally:
            await gen.aclose()
        assert reader.calls == 1  # the initial backfill; no per-second polling


class TestUserStreamSeqIsDurableId:
    """F3 contract (design §5.1.6 / §9.2): the wire ``seq`` a client resumes
    from IS the durable ``events`` row id — end-to-end through a real store."""

    async def test_frame_seq_equals_durable_row_id(self, tmp_path):
        import uuid

        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from src.adapters.sqlalchemy_store.models import Base
        from src.adapters.sqlalchemy_store.store import SQLAlchemyStore
        from src.core.agent_config import AgentConfig
        from src.core.events import Event
        from src.core.types import Message, Session, UserMessage
        from valuz_agent.adapters.data_service_local import LocalDataServiceReader

        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'k.db'}")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        store = SQLAlchemyStore(async_sessionmaker(engine, expire_on_commit=False))

        owner = "user-A"
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
        # ``append_event`` returns the durable autoincrement row id (the seq).
        seq_started = await store.append_event(
            owner, sess.id, msg.id, Event(type="user_message", data={})
        )
        await store.append_event(owner, sess.id, msg.id, Event(type="text_delta", data={}))
        seq_finished = await store.append_event(
            owner, sess.id, msg.id, Event(type="session_idle", data={})
        )

        bind_data_reader(LocalDataServiceReader(store))
        try:
            frames = await adapter.list_user_events_after(owner)
        finally:
            bind_data_reader(None)
            await engine.dispose()

        by_type = {f.event_type: f for f in frames}
        # The control-plane frame seq == the durable row id append_event returned.
        assert by_type["run.started"].seq == seq_started
        assert by_type["run.finished"].seq == seq_finished
        # And the non-lifecycle delta never reached the control plane at all.
        assert set(by_type) == {"run.started", "run.finished"}
