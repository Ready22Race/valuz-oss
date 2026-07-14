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
    async def test_backfill_frames_then_advance_cursor(self, bind_reader):
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

    async def test_disconnect_predicate_stops_the_loop(self, bind_reader):
        bind_reader([])  # nothing to emit
        gen = adapter.iter_user_events_sse("user-A", is_disconnected=lambda: True)
        with pytest.raises(StopAsyncIteration):
            await anext(gen)

    async def test_no_db_session_held_between_polls(self, bind_reader):
        # §9.2: each poll is a DISCRETE read. Proven by the reader being
        # invoked fresh per tick (never a long-lived handle). One backfill
        # read happens before the first yield.
        reader = bind_reader([_ev(1, "s", "user_message")])
        gen = adapter.iter_user_events_sse("user-A", after_seq=0)
        try:
            await anext(gen)
        finally:
            await gen.aclose()
        assert reader.calls == 1  # exactly one discrete read produced the frame
