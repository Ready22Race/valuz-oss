"""Partial assistant history when a turn is interrupted mid-stream."""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

import valuz_agent.boot.kernel  # noqa: F401

from src.adapters.database_sink import DatabaseEventSink
from src.adapters.delta_coalescing_sink import DeltaCoalescingSink
from src.adapters.persist_then_broadcast_sink import PersistThenBroadcastSink
from src.core.events import Event
from src.core.orchestrator import _MessageObserverSink


class _FakeStore:
    def __init__(self) -> None:
        self.appended: list[Event] = []
        self._next_seq = 100

    async def append_event(
        self, user_id: str, session_id: str, message_id: str, event: Event, **kw: object
    ) -> int:
        self.appended.append(event)
        self._next_seq += 1
        return self._next_seq


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[Event] = []

    async def emit(self, event: Event) -> None:
        self.events.append(event)


def _observer() -> tuple[_FakeStore, _RecordingSink, _MessageObserverSink]:
    store = _FakeStore()
    live = _RecordingSink()
    db = DatabaseEventSink(store, "owner-1", "sess-1", "msg-1")
    persist_then_live = PersistThenBroadcastSink(db, live)
    coalesced = DeltaCoalescingSink(persist_then_live)
    return store, live, _MessageObserverSink(coalesced)


async def test_interrupted_turn_persists_partial_assistant_text_before_idle() -> None:
    store, live, observer = _observer()

    await observer.emit(Event(type="text_delta", data={"text": "already "}))
    await observer.emit(Event(type="text_delta", data={"text": "streamed"}))
    await observer.emit(
        Event(
            type="session_idle",
            data={
                "stop_reason": {"type": "error", "category": "user_interrupt"},
                "num_turns": 1,
            },
        )
    )

    assert [event.type for event in store.appended] == ["assistant_message", "session_idle"]
    assert store.appended[0].data == {"text": "already streamed"}
    assert observer.assistant_text == "already streamed"

    assert [event.type for event in live.events] == [
        "text_delta",
        "assistant_message",
        "session_idle",
    ]
    assert "seq" not in live.events[0].data
    assert live.events[1].data["seq"] == 101
    assert live.events[2].data["seq"] == 102


async def test_final_assistant_message_wins_over_streamed_delta() -> None:
    store, live, observer = _observer()

    await observer.emit(Event(type="text_delta", data={"text": "draft"}))
    await observer.emit(Event(type="assistant_message", data={"text": "final"}))
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))

    assert [event.type for event in store.appended] == ["assistant_message", "session_idle"]
    assert store.appended[0].data == {"text": "final"}
    assert observer.assistant_text == "final"
    assert [event.type for event in live.events] == [
        "text_delta",
        "assistant_message",
        "session_idle",
    ]


async def test_partial_after_a_canonical_block_is_persisted_separately() -> None:
    store, live, observer = _observer()

    await observer.emit(Event(type="text_delta", data={"text": "draft one"}))
    await observer.emit(Event(type="assistant_message", data={"text": "final one"}))
    await observer.emit(Event(type="text_delta", data={"text": "partial two"}))
    await observer.emit(
        Event(
            type="session_idle",
            data={
                "stop_reason": {"type": "error", "category": "user_interrupt"},
                "num_turns": 1,
            },
        )
    )

    assert [event.type for event in store.appended] == [
        "assistant_message",
        "assistant_message",
        "session_idle",
    ]
    assert store.appended[0].data == {"text": "final one"}
    assert store.appended[1].data == {"text": "partial two"}
    assert observer.assistant_text == "final one\npartial two"
    assert [event.type for event in live.events] == [
        "text_delta",
        "assistant_message",
        "text_delta",
        "assistant_message",
        "session_idle",
    ]
