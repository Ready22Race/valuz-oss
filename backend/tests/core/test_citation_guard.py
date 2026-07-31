"""Citation evidence registry and final-answer guard."""

from __future__ import annotations

import json

from src.core.citation import CitationGuard, EvidenceRegistry


def _item(
    handle: str = "ev_revenue_2025",
    *,
    locator: dict | None = None,
) -> dict:
    item = {
        "evidenceHandle": handle,
        "source": {
            "sourceId": "doc-1",
            "providerId": "valuz-project-docs",
            "documentId": "doc-1",
            "documentVersion": "sha256:abc",
            "sourceType": "document",
            "mimeType": "application/pdf",
            "title": "Annual Report",
            "retrievedAt": "2026-07-30T10:00:00Z",
        },
        "evidence": {
            "kind": "text",
            "quote": "Revenue increased by 12%.",
            "snippet": "Revenue increased by 12%.",
            "capturedAt": "2026-07-30T10:00:00Z",
            "contentHash": "sha256:abc",
        },
    }
    if locator is not None:
        item["locator"] = locator
    return item


def _registry(*items: dict) -> EvidenceRegistry:
    registry = EvidenceRegistry()
    payload = [{"snippet": "visible to model", "_valuz_evidence": item} for item in items]
    assert registry.register_tool_result(
        json.dumps(payload), tool_name="valuz_docs/doc_search"
    ) == len(items)
    return registry


def test_registry_accepts_nested_valid_envelope_and_first_writer_wins() -> None:
    registry = _registry(_item(locator={"kind": "pdf", "page": 12}))
    replacement = _item(locator={"kind": "pdf", "page": 99})

    assert (
        registry.register_tool_result(
            {"result": {"_valuz_evidence": replacement}},
            tool_name="untrusted/second",
        )
        == 0
    )
    record = registry.get("ev_revenue_2025")
    assert record is not None
    assert record.locator == {"kind": "pdf", "page": 12}
    assert record.tool_name == "valuz_docs/doc_search"


def test_registry_decodes_json_nested_in_mcp_text_content_blocks() -> None:
    envelope_json = json.dumps(
        {"result": {"_valuz_evidence": _item()}},
        ensure_ascii=False,
    )
    mcp_result = {
        "content": [
            {
                "type": "text",
                "text": envelope_json,
            }
        ]
    }
    registry = EvidenceRegistry()

    assert (
        registry.register_tool_result(
            json.dumps(mcp_result, ensure_ascii=False),
            tool_name="valuz-search/document_fetch",
        )
        == 1
    )
    record = registry.get("ev_revenue_2025")
    assert record is not None
    assert record.tool_name == "valuz-search/document_fetch"


def test_registry_ignores_malformed_and_non_json_results() -> None:
    registry = EvidenceRegistry()

    assert registry.register_tool_result("not json") == 0
    assert (
        registry.register_tool_result(
            {"_valuz_evidence": {"evidenceHandle": "bad", "source": {}, "evidence": {}}}
        )
        == 0
    )
    assert len(registry) == 0


def test_registry_rejects_oversized_snapshots_and_locator_geometry() -> None:
    oversized = _item(locator={"kind": "pdf", "page": 1})
    oversized["evidence"]["quote"] = "x" * 32_001
    too_many_rects = _item(
        "ev_rects_12345678",
        locator={
            "kind": "pdf",
            "page": 1,
            "rects": [{"x": 0.1, "y": 0.1, "width": 0.1, "height": 0.1} for _ in range(129)],
        },
    )
    registry = EvidenceRegistry()

    assert registry.register_tool_result({"_valuz_evidence": [oversized, too_many_rects]}) == 0
    assert len(registry) == 0


def test_registry_never_persists_signed_urls_paths_or_unknown_locator_fields() -> None:
    item = _item(
        locator={
            "kind": "pdf",
            "page": 12,
            "url": "https://private.invalid/file?token=secret",
            "absPath": "/Users/private/report.pdf",
        }
    )
    item["source"]["canonicalUrl"] = "https://private.invalid/report.pdf?X-Amz-Signature=secret"
    item["source"]["fileUrl"] = "https://private.invalid/file?token=secret"
    item["evidence"]["rawPayload"] = {"api_key": "secret"}

    registry = _registry(item)
    record = registry.get("ev_revenue_2025")

    assert record is not None
    assert "canonicalUrl" not in record.source
    assert "fileUrl" not in record.source
    assert "rawPayload" not in record.evidence
    assert record.locator == {"kind": "pdf", "page": 12}


def test_registry_rejects_evidence_outside_locked_document_scope() -> None:
    registry = EvidenceRegistry(allowed_document_ids={"doc-1"})
    allowed = _item("ev_allowed_12345678")
    outside = _item("ev_outside_12345678")
    outside["source"] = {
        **outside["source"],
        "sourceId": "doc-2",
        "documentId": "doc-2",
    }
    dataset = _item("ev_dataset_12345678")
    dataset["source"] = {
        **dataset["source"],
        "sourceId": "dataset-1",
        "sourceType": "dataset",
    }

    assert (
        registry.register_tool_result(
            {"_valuz_evidence": [allowed, outside, dataset]},
        )
        == 1
    )
    assert registry.get("ev_allowed_12345678") is not None
    assert registry.get("ev_outside_12345678") is None
    assert registry.get("ev_dataset_12345678") is None


def test_guard_binds_known_handle_and_builds_bundle_from_registry() -> None:
    registry = _registry(_item(locator={"kind": "pdf", "page": 12}))
    guard = CitationGuard(
        registry,
        message_id="msg-1",
        user_prompt="What changed?",
        policy_available=True,
    )

    result = guard.finalize("Revenue increased [Annual Report](evidence://ev_revenue_2025).")

    assert "evidence://" not in result.text
    assert "citation://cit_" in result.text
    assert result.bundle is not None
    assert result.bundle["integrity"] == {
        "status": "passed",
        "unknownCitationIds": [],
        "unusedCitationIds": [],
        "missingLocatorCitationIds": [],
        "repairAttempts": 0,
        "policyRevision": "citation-v1",
    }
    citation = result.bundle["citations"][0]
    assert citation["source"]["title"] == "Annual Report"
    assert citation["evidence"]["quote"] == "Revenue increased by 12%."
    assert citation["locator"] == {"kind": "pdf", "page": 12}


def test_guard_uses_one_deterministic_repair_for_fallback_marker() -> None:
    registry = _registry(_item(locator={"kind": "chunk", "chunkId": "chunk-1"}))
    guard = CitationGuard(
        registry,
        message_id="msg-1",
        user_prompt="Summarize the document",
        policy_available=True,
    )

    result = guard.finalize("Revenue increased [[evidence:ev_revenue_2025]].")

    assert result.bundle is not None
    assert result.bundle["integrity"]["status"] == "repaired"
    assert result.bundle["integrity"]["repairAttempts"] == 1
    assert "citation://cit_" in result.text


def test_guard_repairs_bare_numbered_claims_from_trusted_source_list() -> None:
    registry = _registry(_item(locator={"kind": "chunk", "chunkId": "chunk-1"}))
    guard = CitationGuard(
        registry,
        message_id="msg-1",
        user_prompt="Give me numbered citations",
        policy_available=True,
    )

    result = guard.finalize(
        "Revenue was 100 USD [1]. Profit was 20 USD [1].\n\n"
        "Sources:\n"
        "[1] [Annual Report](evidence://ev_revenue_2025)"
    )

    assert result.bundle is not None
    citation_id = result.bundle["citations"][0]["citationId"]
    assert f"100 USD [1](citation://{citation_id})" in result.text
    assert f"20 USD [1](citation://{citation_id})" in result.text
    assert (
        f"[1] [Annual Report](citation://{citation_id})"
        in result.text
    )
    assert result.bundle["integrity"]["status"] == "repaired"
    assert result.bundle["integrity"]["repairAttempts"] == 1


def test_guard_does_not_guess_ambiguous_numbered_source_bindings() -> None:
    registry = _registry(
        _item(locator={"kind": "chunk", "chunkId": "chunk-1"}),
        _item(
            "ev_other_12345678",
            locator={"kind": "chunk", "chunkId": "chunk-2"},
        ),
    )
    guard = CitationGuard(
        registry,
        message_id="msg-1",
        user_prompt="Give me numbered citations",
        policy_available=True,
    )

    result = guard.finalize(
        "Revenue was 100 USD [1].\n\n"
        "Sources:\n"
        "[1] [Annual Report](evidence://ev_revenue_2025)\n"
        "[1] [Other Report](evidence://ev_other_12345678)"
    )

    assert "100 USD [1]." in result.text
    assert "100 USD [1](citation://" not in result.text


def test_guard_never_promotes_unknown_model_minted_source() -> None:
    guard = CitationGuard(
        EvidenceRegistry(),
        message_id="msg-1",
        user_prompt="请给出引用",
        policy_available=True,
    )

    result = guard.finalize(
        "Claim [fake](evidence://ev_fake_12345678) and [also fake](citation://cit_model_minted)."
    )

    assert result.text == "Claim fake and also fake."
    assert result.bundle is not None
    assert result.bundle["citations"] == []
    assert result.bundle["integrity"]["status"] == "degraded"
    assert result.bundle["integrity"]["unknownCitationIds"] == [
        "ev_fake_12345678",
        "cit_model_minted",
    ]


def test_guard_marks_missing_document_locator_and_unused_evidence() -> None:
    registry = _registry(
        _item(),
        _item(
            "ev_other_12345678",
            locator={"kind": "chunk", "chunkId": "chunk-other"},
        ),
    )
    guard = CitationGuard(
        registry,
        message_id="msg-1",
        user_prompt="Use the report",
        policy_available=True,
    )

    result = guard.finalize("[report](evidence://ev_revenue_2025)")

    assert result.bundle is not None
    integrity = result.bundle["integrity"]
    assert integrity["status"] == "degraded"
    assert len(integrity["missingLocatorCitationIds"]) == 1
    assert len(integrity["unusedCitationIds"]) == 1


def test_guard_does_not_add_bundle_to_ordinary_chat() -> None:
    guard = CitationGuard(
        EvidenceRegistry(),
        message_id="msg-1",
        user_prompt="你好",
        policy_available=True,
    )

    result = guard.finalize("你好！")

    assert result.text == "你好！"
    assert result.bundle is None


def test_guard_requires_citations_for_locked_document_research_without_evidence() -> None:
    guard = CitationGuard(
        EvidenceRegistry(allowed_document_ids={"doc-1"}),
        message_id="msg-1",
        user_prompt="What is revenue?",
        policy_available=True,
        force_required=True,
    )

    result = guard.finalize("Revenue was 100.")

    assert result.bundle is not None
    assert result.bundle["citations"] == []
    assert result.bundle["integrity"]["status"] == "degraded"


def test_guard_fails_closed_when_required_skill_is_unavailable() -> None:
    registry = _registry(_item(locator={"kind": "pdf", "page": 1}))
    guard = CitationGuard(
        registry,
        message_id="msg-1",
        user_prompt="Use evidence",
        policy_available=False,
    )

    result = guard.finalize("[report](evidence://ev_revenue_2025)")

    assert result.bundle is not None
    assert result.bundle["integrity"]["status"] == "degraded"


def test_guard_promotes_calculation_input_handles_to_canonical_dependencies() -> None:
    left = _item(
        "ev_left_12345678",
        locator={"kind": "chunk", "chunkId": "left"},
    )
    left["source"]["sourceType"] = "dataset"
    left["source"].pop("documentId")
    left["source"].pop("documentVersion")
    left["evidence"] = {
        "kind": "structured-data",
        "datasetId": "financials",
        "toolName": "stock.income_statement",
        "recordKey": "issuer:2025",
        "field": "current",
        "value": 120,
        "unit": "USDm",
        "period": "FY2025",
        "capturedAt": "2026-07-30T10:00:00Z",
    }
    right = json.loads(json.dumps(left))
    right["evidenceHandle"] = "ev_right_12345678"
    right["source"]["sourceId"] = "dataset-2"
    right["evidence"]["recordKey"] = "issuer:2024"
    right["evidence"]["field"] = "prior"
    right["evidence"]["value"] = 100
    calculation = _item("ev_calculation_12345678")
    calculation["source"]["sourceId"] = "runtime-calc"
    calculation["source"]["providerId"] = "runtime"
    calculation["source"]["sourceType"] = "tool-result"
    calculation["source"].pop("documentId")
    calculation["source"].pop("documentVersion")
    calculation["evidence"] = {
        "kind": "calculation",
        "expression": "((current / prior) - 1) * 100",
        "inputs": [
            {
                "name": "current",
                "citationId": "ev_left_12345678",
                "value": 120,
                "unit": "USDm",
            },
            {
                "name": "prior",
                "citationId": "ev_right_12345678",
                "value": 100,
                "unit": "USDm",
            },
        ],
        "result": 20,
        "unit": "%",
        "rounding": "2dp",
        "calculatedAt": "2026-07-30T10:00:00Z",
    }
    registry = _registry(left, right, calculation)
    guard = CitationGuard(
        registry,
        message_id="msg-calc",
        user_prompt="Calculate growth with citations",
        policy_available=True,
    )

    result = guard.finalize("Growth was 20% [calculation](evidence://ev_calculation_12345678).")

    assert result.bundle is not None
    citations = {
        citation["source"]["sourceId"]: citation for citation in result.bundle["citations"]
    }
    calculation_citation = citations["runtime-calc"]
    input_ids = [item["citationId"] for item in calculation_citation["evidence"]["inputs"]]
    assert input_ids == [
        citations["doc-1"]["citationId"],
        citations["dataset-2"]["citationId"],
    ]
    assert result.bundle["integrity"]["unknownCitationIds"] == []
    assert result.bundle["integrity"]["unusedCitationIds"] == []
