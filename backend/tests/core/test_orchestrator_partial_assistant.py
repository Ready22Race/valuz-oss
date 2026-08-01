"""Partial assistant history when a turn is interrupted mid-stream."""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

import json

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


def _observer_with_citations() -> tuple[_FakeStore, _RecordingSink, _MessageObserverSink]:
    store = _FakeStore()
    live = _RecordingSink()
    db = DatabaseEventSink(store, "owner-1", "sess-1", "msg-1")
    persist_then_live = PersistThenBroadcastSink(db, live)
    coalesced = DeltaCoalescingSink(persist_then_live)
    observer = _MessageObserverSink(
        coalesced,
        message_id="msg-1",
        user_prompt="根据文档回答并引用",
        citation_policy_available=True,
    )
    return store, live, observer


def _observer_with_strict_policy() -> tuple[_FakeStore, _RecordingSink, _MessageObserverSink]:
    store = _FakeStore()
    live = _RecordingSink()
    db = DatabaseEventSink(store, "owner-1", "sess-1", "msg-1")
    persist_then_live = PersistThenBroadcastSink(db, live)
    observer = _MessageObserverSink(
        DeltaCoalescingSink(persist_then_live),
        message_id="msg-1",
        user_prompt="请核验财务数据",
        citation_policy_available=True,
        citation_quality_policy={
            "policy_id": "strict-test",
            "revision": "strict-test-v1",
            "mode": "strict-domain",
            "config": {
                "rules": {
                    "factual_claim": {"citation_required": True},
                    "numeric_claim": {
                        "require_unit": True,
                        "require_period_or_as_of": True,
                        "require_value_in_answer": True,
                    },
                },
                "failure": {"publish_on_degraded": "draft_only"},
            },
        },
    )
    return store, live, observer


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


async def test_final_assistant_message_captures_citation_bundle() -> None:
    _store, _live, observer = _observer()

    await observer.emit(
        Event(
            type="assistant_message",
            data={"text": "final"},
        )
    )
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))

    assert observer.citation_bundle is None


async def test_final_assistant_is_guarded_before_persistence_and_broadcast() -> None:
    store, live, observer = _observer_with_citations()
    evidence = {
        "_valuz_evidence": {
            "evidenceHandle": "ev_revenue_2025",
            "source": {
                "sourceId": "doc-1",
                "providerId": "docs",
                "documentId": "doc-1",
                "sourceType": "document",
                "title": "Report",
                "retrievedAt": "2026-07-30T10:00:00Z",
            },
            "evidence": {
                "kind": "text",
                "quote": "Revenue increased.",
                "snippet": "Revenue increased.",
                "capturedAt": "2026-07-30T10:00:00Z",
            },
            "locator": {"kind": "pdf", "page": 1},
        }
    }
    await observer.emit(Event(type="tool_use", data={"id": "tool-1", "name": "doc_search"}))
    await observer.emit(
        Event(type="tool_result", data={"id": "tool-1", "content": json.dumps(evidence)})
    )
    await observer.emit(
        Event(
            type="assistant_message",
            data={"text": "Claim [report](evidence://ev_revenue_2025)."},
        )
    )
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))

    assistant = next(event for event in store.appended if event.type == "assistant_message")
    assert "evidence://" not in assistant.data["text"]
    assert "citation://cit_" in assistant.data["text"]
    assert assistant.data["citation_bundle"]["integrity"]["status"] == "passed"
    assert observer.citation_bundle == assistant.data["citation_bundle"]
    live_assistant = next(event for event in live.events if event.type == "assistant_message")
    assert live_assistant.data["citation_bundle"] == assistant.data["citation_bundle"]


async def test_private_citation_content_is_registered_but_not_forwarded() -> None:
    store, live, observer = _observer_with_citations()
    evidence = {
        "_valuz_evidence": {
            "evidenceHandle": "ev_persisted_2025",
            "source": {
                "sourceId": "doc-1",
                "providerId": "docs",
                "documentId": "doc-1",
                "sourceType": "document",
                "title": "Report",
                "retrievedAt": "2026-07-30T10:00:00Z",
            },
            "evidence": {
                "kind": "text",
                "quote": "Revenue increased.",
                "snippet": "Revenue increased.",
                "capturedAt": "2026-07-30T10:00:00Z",
            },
            "locator": {"kind": "pdf", "page": 1},
        }
    }
    await observer.emit(Event(type="tool_use", data={"id": "tool-1", "name": "doc_search"}))
    await observer.emit(
        Event(
            type="tool_result",
            data={
                "id": "tool-1",
                "content": "<persisted-output>placeholder</persisted-output>",
                "_citation_content": json.dumps(evidence),
            },
        )
    )
    await observer.emit(
        Event(
            type="assistant_message",
            data={"text": "Claim [report](evidence://ev_persisted_2025)."},
        )
    )
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))

    tool_result = next(event for event in store.appended if event.type == "tool_result")
    assert "_citation_content" not in tool_result.data
    assert (
        "_citation_content"
        not in next(event for event in live.events if event.type == "tool_result").data
    )
    assistant = next(event for event in store.appended if event.type == "assistant_message")
    assert "citation://cit_" in assistant.data["text"]
    assert len(assistant.data["citation_bundle"]["citations"]) == 1


async def test_uncited_evidence_answer_is_withheld_then_repaired_once() -> None:
    store, live, observer = _observer_with_citations()
    evidence = {
        "_valuz_evidence": {
            "evidenceHandle": "ev_retry_2025",
            "source": {
                "sourceId": "doc-1",
                "providerId": "docs",
                "documentId": "doc-1",
                "sourceType": "document",
                "title": "Report",
                "retrievedAt": "2026-07-30T10:00:00Z",
            },
            "evidence": {
                "kind": "text",
                "quote": "Revenue increased.",
                "snippet": "Revenue increased.",
                "capturedAt": "2026-07-30T10:00:00Z",
            },
            "locator": {"kind": "pdf", "page": 1},
        }
    }
    await observer.emit(Event(type="tool_use", data={"id": "tool-1", "name": "doc_search"}))
    await observer.emit(
        Event(type="tool_result", data={"id": "tool-1", "content": json.dumps(evidence)})
    )
    await observer.emit(Event(type="assistant_message", data={"text": "Uncited draft."}))
    await observer.emit(
        Event(
            type="session_idle",
            data={"stop_reason": {"type": "end_turn"}, "num_turns": 1},
        )
    )

    assert observer.citation_repair_requested is True
    assert not any(event.type == "assistant_message" for event in store.appended)
    assert not any(event.type == "session_idle" for event in store.appended)
    observer.begin_citation_repair()

    await observer.emit(
        Event(
            type="assistant_message",
            data={"text": "Claim [1](evidence://ev_retry_2025)."},
        )
    )
    await observer.emit(
        Event(
            type="session_idle",
            data={"stop_reason": {"type": "end_turn"}, "num_turns": 1},
        )
    )

    assistant = next(event for event in store.appended if event.type == "assistant_message")
    assert assistant.data["text"].startswith("Claim [1](citation://cit_")
    assert assistant.data["citation_bundle"]["integrity"]["status"] == "repaired"
    assert assistant.data["citation_bundle"]["integrity"]["repairAttempts"] == 1
    assert [event.type for event in store.appended].count("session_idle") == 1
    assert [event.type for event in live.events].count("session_idle") == 1


async def test_one_valid_citation_does_not_hide_another_uncited_claim() -> None:
    store, _live, observer = _observer_with_citations()
    evidence = {
        "_valuz_evidence": {
            "evidenceHandle": "ev_revenue_2025",
            "source": {
                "sourceId": "doc-1",
                "providerId": "docs",
                "documentId": "doc-1",
                "sourceType": "document",
                "title": "Report",
                "retrievedAt": "2026-07-30T10:00:00Z",
            },
            "evidence": {
                "kind": "text",
                "quote": "Revenue increased.",
                "snippet": "Revenue increased.",
                "capturedAt": "2026-07-30T10:00:00Z",
            },
            "locator": {"kind": "pdf", "page": 1},
        }
    }
    await observer.emit(Event(type="tool_use", data={"id": "tool-1", "name": "doc_search"}))
    await observer.emit(
        Event(type="tool_result", data={"id": "tool-1", "content": json.dumps(evidence)})
    )
    await observer.emit(
        Event(
            type="assistant_message",
            data={
                "text": (
                    "Revenue increased [report](evidence://ev_revenue_2025). Alice is the CEO."
                )
            },
        )
    )
    await observer.emit(
        Event(
            type="session_idle",
            data={"stop_reason": {"type": "end_turn"}, "num_turns": 1},
        )
    )

    assert observer.citation_repair_requested is True
    assert not any(event.type == "assistant_message" for event in store.appended)


async def test_strict_policy_repairs_claim_local_quality_issue_even_with_valid_citation() -> None:
    store, _live, observer = _observer_with_strict_policy()
    evidence = {
        "_valuz_evidence": {
            "evidenceHandle": "ev_strict_revenue_2025",
            "source": {
                "sourceId": "financials",
                "providerId": "data",
                "sourceType": "dataset",
                "title": "Income statement",
                "retrievedAt": "2026-08-01T08:00:00Z",
            },
            "evidence": {
                "kind": "structured-data",
                "datasetId": "financials",
                "toolName": "income_statement",
                "recordKey": "issuer|2025 FY",
                "field": "revenue",
                "value": 120,
                "period": "2025 FY",
                "capturedAt": "2026-08-01T08:00:00Z",
            },
        }
    }
    await observer.emit(Event(type="tool_use", data={"id": "tool-1", "name": "stock"}))
    await observer.emit(
        Event(type="tool_result", data={"id": "tool-1", "content": json.dumps(evidence)})
    )
    await observer.emit(
        Event(
            type="assistant_message",
            data={
                "text": "Revenue was 120 in 2025 [data](evidence://ev_strict_revenue_2025)."
            },
        )
    )
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))

    assert observer.citation_repair_requested is True
    assert not any(event.type == "assistant_message" for event in store.appended)
    assert "numeric_unit_missing" in observer.citation_repair_prompt


async def test_citation_delta_only_draft_is_hidden_and_replaced_after_repair() -> None:
    store, live, observer = _observer_with_citations()
    evidence = {
        "_valuz_evidence": {
            "evidenceHandle": "ev_delta_retry_2025",
            "source": {
                "sourceId": "doc-1",
                "providerId": "docs",
                "documentId": "doc-1",
                "sourceType": "document",
                "title": "Report",
                "retrievedAt": "2026-07-30T10:00:00Z",
            },
            "evidence": {
                "kind": "text",
                "quote": "Revenue increased.",
                "snippet": "Revenue increased.",
                "capturedAt": "2026-07-30T10:00:00Z",
            },
            "locator": {"kind": "pdf", "page": 1},
        }
    }
    await observer.emit(Event(type="tool_use", data={"id": "tool-1", "name": "doc_search"}))
    await observer.emit(
        Event(type="tool_result", data={"id": "tool-1", "content": json.dumps(evidence)})
    )

    await observer.emit(Event(type="text_delta", data={"text": "Uncited first draft."}))
    await observer.emit(
        Event(
            type="session_idle",
            data={"stop_reason": {"type": "end_turn"}, "num_turns": 1},
        )
    )

    assert observer.citation_repair_requested is True
    assert not any(event.type in {"text_delta", "assistant_message"} for event in live.events)
    observer.begin_citation_repair()

    await observer.emit(
        Event(
            type="text_delta",
            data={"text": "Revenue increased [1](evidence://ev_delta_retry_2025)."},
        )
    )
    await observer.emit(
        Event(
            type="session_idle",
            data={"stop_reason": {"type": "end_turn"}, "num_turns": 1},
        )
    )

    assistants = [event for event in live.events if event.type == "assistant_message"]
    assert len(assistants) == 1
    assert "Uncited first draft" not in assistants[0].data["text"]
    assert assistants[0].data["text"].startswith("Revenue increased [1](citation://cit_")
    assert not any(event.type == "text_delta" for event in live.events)
    assert [event.type for event in store.appended].count("assistant_message") == 1


async def test_second_uncited_answer_is_fail_closed() -> None:
    store, _live, observer = _observer_with_citations()
    evidence = {
        "_valuz_evidence": {
            "evidenceHandle": "ev_retry_2025",
            "source": {
                "sourceId": "doc-1",
                "providerId": "docs",
                "documentId": "doc-1",
                "sourceType": "document",
                "title": "Report",
                "retrievedAt": "2026-07-30T10:00:00Z",
            },
            "evidence": {
                "kind": "text",
                "quote": "Revenue increased.",
                "snippet": "Revenue increased.",
                "capturedAt": "2026-07-30T10:00:00Z",
            },
            "locator": {"kind": "pdf", "page": 1},
        }
    }
    await observer.emit(Event(type="tool_use", data={"id": "tool-1", "name": "doc_search"}))
    await observer.emit(
        Event(type="tool_result", data={"id": "tool-1", "content": json.dumps(evidence)})
    )
    for attempt in range(2):
        await observer.emit(Event(type="assistant_message", data={"text": "Uncited draft."}))
        await observer.emit(
            Event(
                type="session_idle",
                data={"stop_reason": {"type": "end_turn"}, "num_turns": 1},
            )
        )
        if attempt == 0:
            observer.begin_citation_repair()

    assistant = next(event for event in store.appended if event.type == "assistant_message")
    assert assistant.data["text"].startswith("Citation verification failed")
    assert assistant.data["citation_bundle"]["integrity"]["publicationBlocked"] is True


async def test_second_semantically_mismatched_citation_is_fail_closed() -> None:
    store, _live, observer = _observer_with_strict_policy()
    evidence = {
        "_valuz_evidence": {
            "evidenceHandle": "ev_wrong_financial_field",
            "source": {
                "sourceId": "financials",
                "providerId": "data",
                "sourceType": "dataset",
                "title": "Income statement",
                "retrievedAt": "2026-08-01T08:00:00Z",
            },
            "evidence": {
                "kind": "structured-data",
                "datasetId": "financials",
                "toolName": "income_statement",
                "recordKey": "issuer|2025 FY",
                "field": "fiscal_year",
                "value": 2025,
                "period": "2025 FY",
                "capturedAt": "2026-08-01T08:00:00Z",
            },
        }
    }
    await observer.emit(Event(type="tool_use", data={"id": "tool-1", "name": "stock"}))
    await observer.emit(
        Event(type="tool_result", data={"id": "tool-1", "content": json.dumps(evidence)})
    )
    for attempt in range(2):
        await observer.emit(
            Event(
                type="assistant_message",
                data={
                    "text": (
                        "Revenue was 2025 USD in 2025 "
                        "[data](evidence://ev_wrong_financial_field)."
                    )
                },
            )
        )
        await observer.emit(
            Event(
                type="session_idle",
                data={"stop_reason": {"type": "end_turn"}, "num_turns": 1},
            )
        )
        if attempt == 0:
            assert observer.citation_repair_requested is True
            observer.begin_citation_repair()

    assistant = next(event for event in store.appended if event.type == "assistant_message")
    bundle = assistant.data["citation_bundle"]
    assert assistant.data["text"].startswith("Citation verification failed")
    assert bundle["integrity"]["status"] == "repaired"
    assert bundle["integrity"]["repairAttempts"] == 1
    assert bundle["integrity"]["publicationBlocked"] is True
    assert bundle["quality"]["publishStatus"] == "blocked"


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
