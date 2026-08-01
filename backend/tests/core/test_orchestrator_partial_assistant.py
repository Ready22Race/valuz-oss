"""Partial assistant history when a turn is interrupted mid-stream."""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

import json

import valuz_agent.boot.kernel  # noqa: F401

from src.adapters.database_sink import DatabaseEventSink
from src.adapters.delta_coalescing_sink import DeltaCoalescingSink
from src.adapters.persist_then_broadcast_sink import PersistThenBroadcastSink
from src.core.events import Event
from src.core.orchestrator import _MessageObserverSink, _sanitize_citation_repair_prose


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


def test_repair_prose_sanitizer_covers_internal_protocol_synonyms() -> None:
    for internal_term in (
        "evidenceHandle",
        "evidence handle",
        "citationId",
        "证据句柄",
        "引用句柄",
        "证据记录",
        "独立证据凭证",
        "合规绑定",
        "经认证的引用",
        "可引用来源",
        "行内引用",
        "嵌套财务子字段",
        "工具原始返回",
        "valuz.quality-claim.invalid",
        "[UNSOURCED]",
    ):
        result = _sanitize_citation_repair_prose(f"安全结论。\n\n诊断：{internal_term}。")
        assert result.startswith("安全结论。")
        assert "来源定位不完整" in result
        assert internal_term not in result


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


async def test_internal_compaction_handoff_is_not_persisted_or_broadcast() -> None:
    store, live, observer = _observer()
    handoff = """## SESSION INTENT
Research the filing.

## SUMMARY
Internal state.

## ARTIFACTS
None.

## NEXT STEPS
Continue with tools.
"""

    await observer.emit(Event(type="assistant_message", data={"text": handoff}))
    await observer.emit(Event(type="tool_use", data={"id": "tool-1", "name": "search"}))
    await observer.emit(Event(type="assistant_message", data={"text": "Visible answer."}))
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))

    assistants = [event for event in store.appended if event.type == "assistant_message"]
    assert [event.data["text"] for event in assistants] == ["Visible answer."]
    assert "SESSION INTENT" not in observer.assistant_text
    assert all("SESSION INTENT" not in str(event.data.get("text") or "") for event in live.events)


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


async def test_unrelated_oversized_tool_result_does_not_retry_clean_cited_answer() -> None:
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
    await observer.emit(Event(type="tool_use", data={"id": "tool-1", "name": "docs"}))
    await observer.emit(
        Event(type="tool_result", data={"id": "tool-1", "content": json.dumps(evidence)})
    )
    await observer.emit(Event(type="tool_use", data={"id": "tool-2", "name": "other"}))
    await observer.emit(
        Event(
            type="tool_result",
            data={
                "id": "tool-2",
                "content": '{"_valuz_evidence":' + ("x" * 2_000_100),
            },
        )
    )
    await observer.emit(
        Event(
            type="assistant_message",
            data={"text": "Revenue increased [source](evidence://ev_revenue_2025)."},
        )
    )
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))

    assert observer.citation_repair_requested is False
    assistant = next(event for event in store.appended if event.type == "assistant_message")
    assert assistant.data["citation_bundle"]["quality"]["status"] == "passed"
    assert assistant.data["citation_bundle"]["integrity"]["evidenceOverflowReasons"]


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


async def test_large_private_citation_content_registers_evidence_without_forwarding() -> None:
    store, live, observer = _observer_with_citations()
    evidence = {
        "padding": "x" * 2_100_000,
        "_valuz_evidence": {
            "evidenceHandle": "ev_large_persisted_2025",
            "source": {
                "sourceId": "transcript-1",
                "providerId": "search",
                "documentId": "transcript-1",
                "sourceType": "document",
                "title": "Earnings call transcript",
                "retrievedAt": "2026-07-30T10:00:00Z",
            },
            "evidence": {
                "kind": "text",
                "quote": "Cloud revenue increased by 20%.",
                "snippet": "Cloud revenue increased by 20%.",
                "capturedAt": "2026-07-30T10:00:00Z",
            },
            "locator": {"kind": "chunk", "chunkId": "chunk-1"},
        },
    }
    await observer.emit(Event(type="tool_use", data={"id": "tool-1", "name": "search"}))
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
            data={
                "text": "Cloud revenue increased by 20% [1](evidence://ev_large_persisted_2025)."
            },
        )
    )
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))

    tool_result = next(event for event in store.appended if event.type == "tool_result")
    assert "_citation_content" not in tool_result.data
    assert "padding" not in str(tool_result.data)
    assert "padding" not in str(next(e for e in live.events if e.type == "tool_result").data)
    assistant = next(event for event in store.appended if event.type == "assistant_message")
    assert len(assistant.data["citation_bundle"]["citations"]) == 1
    assert assistant.data["citation_bundle"]["integrity"]["evidenceRejectedCount"] == 0


async def test_source_tool_result_persists_compact_evidence_but_seals_full_snapshot() -> None:
    store, live, observer = _observer_with_citations()
    payload = {
        "_valuz_evidence": {
            "evidenceHandle": "ev_compact_revenue_2025",
            "source": {
                "sourceId": "financials:issuer",
                "providerId": "valuz-stock",
                "sourceType": "dataset",
                "title": "Income statement",
                "retrievedAt": "2026-08-01T08:00:00Z",
            },
            "evidence": {
                "kind": "structured-data",
                "datasetId": "financials",
                "toolName": "income_statement",
                "recordKey": "issuer|FY2025",
                "field": "revenue",
                "metric": "revenue",
                "value": 120,
                "unit": "USDm",
                "period": "FY2025",
                "capturedAt": "2026-08-01T08:00:00Z",
            },
        },
        "data": [{"revenue": 120}],
    }
    await observer.emit(Event(type="tool_use", data={"id": "tool-1", "name": "income"}))
    await observer.emit(
        Event(
            type="tool_result",
            data={"id": "tool-1", "content": json.dumps(payload)},
        )
    )
    await observer.emit(
        Event(
            type="assistant_message",
            data={"text": "FY2025 revenue was 120 USDm [1](evidence://ev_compact_revenue_2025)."},
        )
    )
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))

    persisted = next(event for event in store.appended if event.type == "tool_result")
    visible = json.loads(persisted.data["content"])
    assert visible["_valuz_evidence"] == [
        {
            "evidenceHandle": "ev_compact_revenue_2025",
            "kind": "structured-data",
            "field": "revenue",
            "metric": "revenue",
            "value": 120,
            "unit": "USDm",
            "period": "FY2025",
            "recordKey": "issuer|FY2025",
            "sourceTitle": "Income statement",
        }
    ]
    assert visible["data"] == [{"revenue": 120}]
    assert "providerId" not in persisted.data["content"]
    assert (
        "providerId"
        not in next(event for event in live.events if event.type == "tool_result").data["content"]
    )
    assistant = next(event for event in store.appended if event.type == "assistant_message")
    citation = assistant.data["citation_bundle"]["citations"][0]
    assert citation["source"]["providerId"] == "valuz-stock"
    assert citation["evidence"]["capturedAt"] == "2026-08-01T08:00:00Z"


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
    assert "Never expose evidence handles" in observer.citation_repair_prompt
    assert "Never emit internal markers" in observer.citation_repair_prompt
    assert "Keep the complete claim and its value outside" in observer.citation_repair_prompt
    assert "Do not delete requested facts or values" in observer.citation_repair_prompt
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
            data={"text": "Revenue was 120 in 2025 [data](evidence://ev_strict_revenue_2025)."},
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


async def test_second_uncited_answer_publishes_degraded_repair() -> None:
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
    assert assistant.data["text"] == "Uncited draft."
    bundle = assistant.data["citation_bundle"]
    assert bundle["integrity"]["status"] == "degraded"
    assert bundle["integrity"]["repairAttempts"] == 1
    assert bundle["integrity"]["repairOutcome"] == "rejected-no-improvement"
    assert "publicationBlocked" not in bundle["integrity"]
    # Baseline OSS does not invent a claim-level issue when no claim was
    # detected; the degraded integrity notice remains the UI fallback.
    assert bundle["quality"]["publishStatus"] == "ready"


async def test_repaired_answer_redacts_internal_citation_protocol_prose() -> None:
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
                "quote": "审计意见为无保留意见。",
                "snippet": "审计意见为无保留意见。",
                "capturedAt": "2026-07-30T10:00:00Z",
            },
            "locator": {"kind": "pdf", "page": 1},
        }
    }
    await observer.emit(Event(type="tool_use", data={"id": "tool-1", "name": "doc_search"}))
    await observer.emit(
        Event(type="tool_result", data={"id": "tool-1", "content": json.dumps(evidence)})
    )
    await observer.emit(Event(type="assistant_message", data={"text": "没有引用的初稿。"}))
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))
    observer.begin_citation_repair()

    await observer.emit(
        Event(
            type="assistant_message",
            data={
                "text": (
                    "审计意见为无保留意见 [source](evidence://ev_retry_2025)。\n\n"
                    "营业收入属于嵌套财务子字段，未附带独立证据记录，"
                    "无法绑定行内引用。"
                )
            },
        )
    )
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))

    assistant = next(event for event in store.appended if event.type == "assistant_message")
    assert "审计意见为无保留意见" in assistant.data["text"]
    assert "citation://cit_" in assistant.data["text"]
    assert "来源定位不完整" in assistant.data["text"]
    for internal_term in ("嵌套财务子字段", "证据记录", "行内引用"):
        assert internal_term not in assistant.data["text"]


async def test_second_semantically_mismatched_citation_publishes_degraded_repair() -> None:
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
                        "Revenue was 2025 USD in 2025 [data](evidence://ev_wrong_financial_field)."
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
    assert assistant.data["text"].startswith("Revenue was 2025 USD in 2025 [data](citation://cit_")
    assert bundle["integrity"]["status"] == "degraded"
    assert bundle["integrity"]["repairAttempts"] == 1
    assert bundle["integrity"]["repairOutcome"] == "rejected-no-improvement"
    assert "publicationBlocked" not in bundle["integrity"]
    assert bundle["quality"]["publishStatus"] == "draft-only"
    assert "claim_evidence_mismatch" in {issue["code"] for issue in bundle["quality"]["issues"]}


async def test_repair_that_increases_claim_problems_is_rejected_in_favor_of_initial_draft() -> None:
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
                "quote": "Revenue increased by 20%.",
                "snippet": "Revenue increased by 20%.",
                "capturedAt": "2026-07-30T10:00:00Z",
            },
            "locator": {"kind": "chunk", "chunkId": "chunk-1"},
        }
    }
    await observer.emit(Event(type="tool_use", data={"id": "tool-1", "name": "docs"}))
    await observer.emit(
        Event(type="tool_result", data={"id": "tool-1", "content": json.dumps(evidence)})
    )
    initial = "Revenue increased by 20% [1](evidence://ev_revenue_2025). CEO is Alice."
    await observer.emit(Event(type="assistant_message", data={"text": initial}))
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))
    assert observer.citation_repair_requested is True
    observer.begin_citation_repair()

    await observer.emit(
        Event(
            type="assistant_message",
            data={
                "text": (
                    "Revenue increased by 20% [1](evidence://ev_revenue_2025). "
                    "CEO is Alice. Margin was 42%."
                )
            },
        )
    )
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))

    assistant = next(event for event in store.appended if event.type == "assistant_message")
    assert "Margin was 42%" not in assistant.data["text"]
    assert "CEO is Alice" in assistant.data["text"]
    assert (
        assistant.data["citation_bundle"]["integrity"]["repairOutcome"] == "rejected-no-improvement"
    )


async def test_repair_cannot_improve_score_by_deleting_all_factual_claims() -> None:
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
                "quote": "Revenue increased by 20%.",
                "snippet": "Revenue increased by 20%.",
                "capturedAt": "2026-07-30T10:00:00Z",
            },
            "locator": {"kind": "chunk", "chunkId": "chunk-1"},
        }
    }
    await observer.emit(Event(type="tool_use", data={"id": "tool-1", "name": "docs"}))
    await observer.emit(
        Event(type="tool_result", data={"id": "tool-1", "content": json.dumps(evidence)})
    )
    initial = (
        "Revenue increased by 20% [1](evidence://ev_revenue_2025). "
        "Margin was 42%."
    )
    await observer.emit(Event(type="assistant_message", data={"text": initial}))
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))
    assert observer.citation_repair_requested is True
    observer.begin_citation_repair()

    await observer.emit(
        Event(
            type="assistant_message",
            data={
                "text": (
                    "部分结果的来源定位不完整，相关内容暂时无法核验。"
                    "请稍后重试，或以原始资料为准。"
                )
            },
        )
    )
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))

    assistant = next(event for event in store.appended if event.type == "assistant_message")
    assert "Revenue increased by 20%" in assistant.data["text"]
    assert "Margin was 42%" in assistant.data["text"]
    assert (
        assistant.data["citation_bundle"]["integrity"]["repairOutcome"]
        == "rejected-no-improvement"
    )


async def test_large_input_skips_second_model_repair_and_publishes_initial_draft() -> None:
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
                "quote": "Revenue increased by 20%.",
                "snippet": "Revenue increased by 20%.",
                "capturedAt": "2026-07-30T10:00:00Z",
            },
            "locator": {"kind": "chunk", "chunkId": "chunk-1"},
        }
    }
    await observer.emit(Event(type="tool_use", data={"id": "tool-1", "name": "docs"}))
    await observer.emit(
        Event(type="tool_result", data={"id": "tool-1", "content": json.dumps(evidence)})
    )
    await observer.emit(
        Event(
            type="usage_update",
            data={"input_tokens": 250_001, "output_tokens": 100},
        )
    )
    await observer.emit(Event(type="assistant_message", data={"text": "CEO is Alice."}))
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))

    assert observer.citation_repair_requested is False
    assistant = next(event for event in store.appended if event.type == "assistant_message")
    integrity = assistant.data["citation_bundle"]["integrity"]
    assert integrity["repairOutcome"] == "skipped"
    assert integrity["repairSkippedReason"] == "input-token-budget"


async def test_wrong_calendar_binding_is_rebound_to_unique_transcript_without_repair() -> None:
    store, _live, observer = _observer_with_strict_policy()
    evidences = [
        {
            "evidenceHandle": "ev_calendar_2025",
            "source": {
                "sourceId": "calendar-msft",
                "providerId": "stock",
                "sourceType": "dataset",
                "title": "Stock earnings calendar · MSFT",
                "retrievedAt": "2026-08-01T08:00:00Z",
            },
            "evidence": {
                "kind": "structured-data",
                "datasetId": "calendar",
                "toolName": "earnings_calendar",
                "recordKey": "MSFT|2025 FY",
                "field": "filing_date",
                "value": "2025-07-30",
                "period": "2025 FY",
                "capturedAt": "2026-08-01T08:00:00Z",
            },
        },
        {
            "evidenceHandle": "ev_transcript_cloud_2025",
            "source": {
                "sourceId": "transcript-msft-q4",
                "providerId": "search",
                "documentId": "transcript-msft-q4",
                "sourceType": "document",
                "title": "Microsoft Q4 earnings call transcript",
                "retrievedAt": "2026-08-01T08:00:00Z",
            },
            "evidence": {
                "kind": "text",
                "quote": "Microsoft cloud revenue increased by 20% in 2025 Q4.",
                "snippet": "Microsoft cloud revenue increased by 20% in 2025 Q4.",
                "capturedAt": "2026-08-01T08:00:00Z",
            },
            "locator": {"kind": "chunk", "chunkId": "q4-cloud"},
        },
    ]
    await observer.emit(Event(type="tool_use", data={"id": "tool-1", "name": "search"}))
    await observer.emit(
        Event(
            type="tool_result",
            data={"id": "tool-1", "content": json.dumps({"_valuz_evidence": evidences})},
        )
    )
    await observer.emit(
        Event(
            type="assistant_message",
            data={
                "text": (
                    "Microsoft cloud revenue increased by 20% in 2025 Q4 "
                    "[1](evidence://ev_calendar_2025)."
                )
            },
        )
    )
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))

    assert observer.citation_repair_requested is False
    assistant = next(event for event in store.appended if event.type == "assistant_message")
    citations = assistant.data["citation_bundle"]["citations"]
    assert [citation["source"]["sourceId"] for citation in citations] == ["transcript-msft-q4"]
    assert citations[0]["annotations"]["binding"]["autoReboundClaimIds"]


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
