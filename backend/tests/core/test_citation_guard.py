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


def test_registry_preserves_structured_semantic_dimensions() -> None:
    item = _item("ev_dimensions_12345678")
    item["source"].update({"sourceType": "dataset"})
    item["evidence"] = {
        "kind": "structured-data",
        "datasetId": "financials",
        "toolName": "company_income_statement",
        "recordKey": "600519|2024 FY",
        "entityId": "600519",
        "entityName": "贵州茅台",
        "field": "operating_revenue",
        "metric": "operating_revenue",
        "value": 174_144_000_000,
        "unit": "CNY",
        "currency": "CNY",
        "scale": 1,
        "period": "2024 FY",
        "scope": "consolidated",
        "basis": "reported",
        "capturedAt": "2026-08-01T08:00:00Z",
    }

    record = _registry(item).get("ev_dimensions_12345678")

    assert record is not None
    assert record.evidence["entityId"] == "600519"
    assert record.evidence["metric"] == "operating_revenue"
    assert record.evidence["scope"] == "consolidated"
    assert record.evidence["basis"] == "reported"


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
    assert registry.rejected_count == 1
    assert registry.had_evidence_activity is True


def test_registry_reports_oversized_evidence_payload_instead_of_silent_drop() -> None:
    registry = EvidenceRegistry()
    registry._MAX_TOOL_RESULT_CHARS = 32

    assert registry.register_tool_result(json.dumps({"_valuz_evidence": _item()})) == 0
    assert registry.rejected_count == 1
    assert registry.overflow_reasons == ("tool_result_invalid_or_oversized",)


def test_unrelated_rejected_tool_payload_does_not_degrade_valid_final_citation() -> None:
    registry = _registry(_item(locator={"kind": "pdf", "page": 1}))
    registry._MAX_TOOL_RESULT_CHARS = 32
    assert registry.register_tool_result(json.dumps({"_valuz_evidence": _item()})) == 0

    guard = CitationGuard(
        registry,
        message_id="msg-1",
        user_prompt="Use the report",
        policy_available=True,
    )
    result = guard.finalize("Revenue [report](evidence://ev_revenue_2025).")

    assert result.bundle is not None
    assert result.bundle["integrity"]["status"] == "passed"
    assert result.bundle["integrity"]["evidenceRejectedCount"] == 1


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
        "evidenceRegisteredCount": 1,
        "evidenceRejectedCount": 0,
        "evidenceOverflowReasons": [],
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
    assert "Sources:" not in result.text
    assert result.text.count(f"citation://{citation_id}") == 2
    assert result.bundle["integrity"]["status"] == "repaired"
    assert result.bundle["integrity"]["repairAttempts"] == 1


def test_guard_removes_redundant_chinese_source_section_and_divider() -> None:
    registry = _registry(_item(locator={"kind": "chunk", "chunkId": "chunk-1"}))
    guard = CitationGuard(
        registry,
        message_id="msg-1",
        user_prompt="请使用引用",
        policy_available=True,
    )

    result = guard.finalize(
        "营收增长 [年报](evidence://ev_revenue_2025)。\n\n"
        "---\n\n"
        "**来源：**\n\n"
        "[1] [年报](evidence://ev_revenue_2025)"
    )

    assert result.bundle is not None
    assert "来源" not in result.text
    assert "\n---" not in result.text
    assert result.text.count("citation://") == 1


def test_guard_preserves_partial_source_section_with_external_links() -> None:
    registry = _registry(_item(locator={"kind": "chunk", "chunkId": "chunk-1"}))
    guard = CitationGuard(
        registry,
        message_id="msg-1",
        user_prompt="请使用引用",
        policy_available=True,
    )

    result = guard.finalize(
        "营收增长 [1]，渠道占比下降 [2]。\n\n"
        "**来源：**\n"
        "[1] [年报](evidence://ev_revenue_2025)\n"
        "[2] [研报](https://example.com/report)"
    )

    assert "来源" in result.text
    assert "https://example.com/report" in result.text


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


def test_guard_removes_protocol_source_placeholders_without_rewriting_prose() -> None:
    registry = _registry(_item(locator={"kind": "chunk", "chunkId": "chunk-1"}))
    guard = CitationGuard(
        registry,
        message_id="msg-1",
        user_prompt="请列出有引用的数据",
        policy_available=True,
    )

    result = guard.finalize(
        "收入同比增长 12%。[1](evidence://ev_revenue_2025) source\n\n"
        "The primary source is the annual report."
    )

    assert "citation://cit_" in result.text
    assert "12%。[1](citation://" in result.text
    assert ") source" not in result.text
    assert "The primary source is the annual report." in result.text


def test_guard_drops_unknown_protocol_label_instead_of_publishing_source() -> None:
    guard = CitationGuard(
        EvidenceRegistry(),
        message_id="msg-1",
        user_prompt="请给出引用",
        policy_available=True,
    )

    result = guard.finalize("结论。[source](evidence://ev_unknown_12345678)")

    assert result.text == "结论。"
    assert result.bundle is not None
    assert result.bundle["integrity"]["unknownCitationIds"] == ["ev_unknown_12345678"]


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
        "entityId": "issuer-1",
        "entityName": "Issuer",
        "metric": "revenue_growth_rate",
        "period": "FY2025",
        "scope": "consolidated",
        "basis": "reported",
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
    assert calculation_citation["evidence"]["metric"] == "revenue_growth_rate"
    assert calculation_citation["evidence"]["entityId"] == "issuer-1"
    assert calculation_citation["evidence"]["scope"] == "consolidated"
    input_ids = [item["citationId"] for item in calculation_citation["evidence"]["inputs"]]
    assert input_ids == [
        citations["doc-1"]["citationId"],
        citations["dataset-2"]["citationId"],
    ]
    assert result.bundle["integrity"]["unknownCitationIds"] == []
    assert result.bundle["integrity"]["unusedCitationIds"] == []


def test_guard_auto_binds_one_unique_structured_candidate_without_model_repair() -> None:
    margin = _item("ev_margin_12345678")
    margin["source"] = {
        "sourceId": "financials:600519:2024",
        "providerId": "market-data",
        "sourceType": "dataset",
        "title": "Financial data",
        "retrievedAt": "2026-08-01T08:00:00Z",
    }
    margin["evidence"] = {
        "kind": "structured-data",
        "datasetId": "financials",
        "toolName": "company_income_statement",
        "recordKey": "600519|2024 FY",
        "field": "gross_margin",
        "value": 23.5,
        "unit": "%",
        "period": "2024 FY",
        "capturedAt": "2026-08-01T08:00:00Z",
    }
    registry = _registry(margin)
    guard = CitationGuard(
        registry,
        message_id="msg-auto-bind",
        user_prompt="What was gross margin?",
        policy_available=True,
    )

    result = guard.finalize("Gross margin was 23.5% in 2024.")

    assert result.bundle is not None
    assert result.text.count("citation://") == 1
    assert result.bundle["integrity"]["repairAttempts"] == 0
    assert result.bundle["quality"]["claims"][0]["status"] == "auto-bound"
    assert result.bundle["quality"]["metrics"]["claimAutoBoundCount"] == 1


def test_guard_rebinds_one_wrong_sibling_field_to_unique_exact_evidence() -> None:
    wrong = _item("ev_end_date_12345678")
    wrong["source"].update({"sourceType": "dataset", "sourceId": "financials:2025"})
    wrong["source"].pop("documentId")
    wrong["source"].pop("documentVersion")
    wrong["evidence"] = {
        "kind": "structured-data",
        "datasetId": "financials",
        "toolName": "company_income_statement",
        "recordKey": "issuer|FY2025",
        "field": "end_date",
        "metric": "end_date",
        "value": "2025-12-31",
        "period": "FY2025",
        "capturedAt": "2026-08-01T08:00:00Z",
    }
    revenue = json.loads(json.dumps(wrong))
    revenue["evidenceHandle"] = "ev_revenue_exact_12345678"
    revenue["evidence"].update(
        {
            "field": "revenue",
            "metric": "revenue",
            "value": 120,
            "unit": "USDm",
        }
    )
    guard = CitationGuard(
        _registry(wrong, revenue),
        message_id="msg-rebind",
        user_prompt="What was revenue?",
        policy_available=True,
    )

    result = guard.finalize(
        "FY2025 revenue was 120 USDm [source](evidence://ev_end_date_12345678)."
    )

    assert result.bundle is not None
    assert "ev_end_date_12345678" not in result.text
    assert len(result.bundle["citations"]) == 1
    citation = result.bundle["citations"][0]
    assert citation["evidence"]["field"] == "revenue"
    assert citation["annotations"]["binding"]["autoReboundClaimIds"]
    assert result.bundle["quality"]["claims"][0]["status"] == "auto-bound"


def test_guard_rebinds_calculation_inputs_to_unique_value_and_unit_fields() -> None:
    template = _item("ev_wrong_current_12345678")
    template["source"].update({"sourceType": "dataset", "sourceId": "financials"})
    template["source"].pop("documentId")
    template["source"].pop("documentVersion")
    template["evidence"] = {
        "kind": "structured-data",
        "datasetId": "financials",
        "toolName": "company_income_statement",
        "recordKey": "issuer|FY2025",
        "field": "end_date",
        "metric": "end_date",
        "value": "2025-12-31",
        "period": "FY2025",
        "capturedAt": "2026-08-01T08:00:00Z",
    }
    wrong_prior = json.loads(json.dumps(template))
    wrong_prior["evidenceHandle"] = "ev_wrong_prior_12345678"
    wrong_prior["evidence"].update(
        {"recordKey": "issuer|FY2024", "value": "2024-12-31", "period": "FY2024"}
    )
    current = json.loads(json.dumps(template))
    current["evidenceHandle"] = "ev_revenue_current_12345678"
    current["evidence"].update(
        {"field": "revenue", "metric": "revenue", "value": 120, "unit": "USDm"}
    )
    prior = json.loads(json.dumps(wrong_prior))
    prior["evidenceHandle"] = "ev_revenue_prior_12345678"
    prior["evidence"].update(
        {"field": "revenue", "metric": "revenue", "value": 100, "unit": "USDm"}
    )
    calculation = _item("ev_growth_calculation_12345678")
    calculation["source"].update(
        {"sourceId": "runtime-calc", "providerId": "runtime", "sourceType": "tool-result"}
    )
    calculation["source"].pop("documentId")
    calculation["source"].pop("documentVersion")
    calculation["evidence"] = {
        "kind": "calculation",
        "expression": "(current - prior) / prior * 100",
        "inputs": [
            {
                "name": "current",
                "citationId": "ev_wrong_current_12345678",
                "value": 120,
                "unit": "USDm",
            },
            {
                "name": "prior",
                "citationId": "ev_wrong_prior_12345678",
                "value": 100,
                "unit": "USDm",
            },
        ],
        "result": 20,
        "unit": "%",
        "rounding": "2dp",
        "calculatedAt": "2026-08-01T08:00:00Z",
    }
    guard = CitationGuard(
        _registry(template, wrong_prior, current, prior, calculation),
        message_id="msg-calc-rebind",
        user_prompt="Calculate growth with citations",
        policy_available=True,
    )

    result = guard.finalize(
        "Growth was 20% [calculation](evidence://ev_growth_calculation_12345678)."
    )

    assert result.bundle is not None
    calculation_citation = next(
        item for item in result.bundle["citations"] if item["evidence"]["kind"] == "calculation"
    )
    revenue_citations = {
        item["evidence"].get("period"): item
        for item in result.bundle["citations"]
        if item["evidence"].get("field") == "revenue"
    }
    assert [item["citationId"] for item in calculation_citation["evidence"]["inputs"]] == [
        revenue_citations["FY2025"]["citationId"],
        revenue_citations["FY2024"]["citationId"],
    ]
    assert len(calculation_citation["annotations"]["binding"]["calculationInputAutoBindings"]) == 2


def test_guard_does_not_auto_bind_ambiguous_structured_candidates() -> None:
    candidates = []
    for handle in ("ev_margin_first_12345678", "ev_margin_second_12345678"):
        item = _item(handle)
        item["source"] = {
            "sourceId": f"financials:{handle}",
            "providerId": "market-data",
            "sourceType": "dataset",
            "title": "Financial data",
            "retrievedAt": "2026-08-01T08:00:00Z",
        }
        item["evidence"] = {
            "kind": "structured-data",
            "datasetId": "financials",
            "toolName": "company_income_statement",
            "recordKey": "600519|2024 FY",
            "field": "gross_margin",
            "value": 23.5,
            "unit": "%",
            "period": "2024 FY",
            "capturedAt": "2026-08-01T08:00:00Z",
        }
        candidates.append(item)
    guard = CitationGuard(
        _registry(*candidates),
        message_id="msg-ambiguous",
        user_prompt="What was gross margin?",
        policy_available=True,
    )

    result = guard.finalize("Gross margin was 23.5% in 2024.")

    assert result.bundle is not None
    assert result.bundle["citations"] == []
    assert "citation://" not in result.text
    assert result.bundle["quality"]["claims"][0]["status"] == "unverified"
    assert "claim_evidence_ambiguous" in {
        issue["code"] for issue in result.bundle["quality"]["issues"]
    }
