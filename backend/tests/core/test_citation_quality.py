from __future__ import annotations

from src.core.citation_quality import evaluate_citation_quality
from src.core.claim_audit import MAX_CLAIMS_PER_ANSWER


def _policy() -> dict:
    return {
        "policy_id": "test-quality",
        "revision": "test-v1",
        "mode": "strict-domain",
        "config": {
            "source_tiers": [
                {
                    "id": "P1",
                    "authority": "primary",
                    "match": {
                        "source_types": ["dataset"],
                        "tools": ["stock.*"],
                    },
                },
                {
                    "id": "P4",
                    "authority": "secondary",
                    "match": {
                        "source_types": ["web"],
                        "tools": ["search.news*"],
                    },
                },
                {
                    "id": "P2",
                    "authority": "issuer",
                    "match": {
                        "any": [
                            {"source_categories": ["filings"]},
                            {"tools": ["search.filings_search"]},
                        ]
                    },
                },
            ],
            "rules": {
                "factual_claim": {"citation_required": True},
                "numeric_claim": {
                    "require_unit": True,
                    "require_period_or_as_of": True,
                    "require_value_in_answer": True,
                },
                "derived_value": {
                    "require_calculation_evidence": True,
                    "require_unit": True,
                    "require_compatible_units": True,
                },
                "low_tier_critical_claim": {
                    "require_cross_check": True,
                    "low_tiers": ["P4"],
                    "cross_check_tiers": ["P1"],
                },
                "time_boundary": {
                    "forbid_extrapolation": True,
                    "require_coverage": True,
                },
            },
            "failure": {"publish_on_degraded": "draft_only"},
        },
    }


def _integrity() -> dict:
    return {
        "status": "passed",
        "unknownCitationIds": [],
        "unusedCitationIds": [],
        "missingLocatorCitationIds": [],
        "repairAttempts": 0,
        "policyRevision": "citation-v1",
    }


def _structured(citation_id: str = "cit_revenue") -> dict:
    return {
        "citationId": citation_id,
        "source": {
            "sourceId": "dataset-1",
            "providerId": "market-data",
            "sourceType": "dataset",
            "title": "Income statement",
            "retrievedAt": "2026-07-30T10:00:00Z",
        },
        "evidence": {
            "kind": "structured-data",
            "datasetId": "financials",
            "toolName": "stock.income_statement",
            "field": "revenue",
            "value": 120,
            "unit": "USDm",
            "period": "FY2025",
            "asOf": "2025-12-31",
            "capturedAt": "2026-07-30T10:00:00Z",
            "coverage": {
                "start": "2025-01-01",
                "end": "2025-12-31",
            },
        },
    }


def test_policy_passes_structured_data_and_adds_stable_annotations() -> None:
    bundle = {
        "version": 1,
        "citations": [_structured()],
        "integrity": _integrity(),
    }

    result = evaluate_citation_quality(
        "Revenue was 120 USDm [source](citation://cit_revenue).",
        bundle,
        _policy(),
    )

    assert result["quality"]["status"] == "passed"
    assert result["quality"]["publishStatus"] == "ready"
    assert result["quality"]["metrics"]["tierCounts"] == {"P1": 1}
    assert result["citations"][0]["annotations"]["quality"] == {
        "policyId": "test-quality",
        "policyRevision": "test-v1",
        "tier": "P1",
        "authority": "primary",
        "status": "passed",
        "label": "P1",
    }
    assert "annotations" not in bundle["citations"][0]


def test_policy_degrades_missing_unit_period_and_out_of_range_date() -> None:
    citation = _structured()
    citation["evidence"].pop("unit")
    citation["evidence"].pop("period")
    citation["evidence"]["asOf"] = "2026-01-02"
    citation["annotations"] = {
        "provenance": {"coverage": {"start": "2025-01-01", "end": "2025-12-31"}}
    }

    result = evaluate_citation_quality(
        "Revenue was 121 [source](citation://cit_revenue).",
        {
            "version": 1,
            "citations": [citation],
            "integrity": _integrity(),
        },
        _policy(),
    )

    codes = {issue["code"] for issue in result["quality"]["issues"]}
    assert {
        "numeric_unit_missing",
        "structured_value_not_present_in_answer",
        "evidence_after_coverage",
    } <= codes
    assert result["quality"]["status"] == "degraded"
    assert result["quality"]["publishStatus"] == "draft-only"


def test_policy_detects_uncited_financial_number_and_missing_coverage() -> None:
    citation = _structured()
    citation["evidence"].pop("coverage")
    result = evaluate_citation_quality(
        ("Revenue was 120 USDm [source](citation://cit_revenue). Margin was 23.5%."),
        {
            "version": 1,
            "citations": [citation],
            "integrity": _integrity(),
        },
        _policy(),
    )

    codes = {issue["code"] for issue in result["quality"]["issues"]}
    assert "numeric_claim_without_citation" in codes
    assert "evidence_coverage_missing" in codes
    numeric_issue = next(
        issue
        for issue in result["quality"]["issues"]
        if issue["code"] == "numeric_claim_without_citation"
    )
    assert numeric_issue["claim"] == {"exact": "Margin was 23.5%."}
    assert result["quality"]["metrics"]["unsourcedClaimCount"] == 1
    claims = {claim["exact"]: claim for claim in result["quality"]["claims"]}
    assert claims["Revenue was 120 USDm."]["status"] == "degraded"
    assert "evidence_coverage_missing" in claims["Revenue was 120 USDm."]["issueCodes"]
    assert claims["Margin was 23.5%."]["status"] == "unsupported"
    assert {
        key: claims["Margin was 23.5%."]["location"][key]
        for key in ("kind", "blockIndex", "start", "end")
    } == {
        "kind": "text",
        "blockIndex": 0,
        "start": 22,
        "end": 39,
    }


def test_policy_audits_non_numeric_external_facts_and_dates() -> None:
    result = evaluate_citation_quality(
        "The company was founded in 1999. Alice is the CEO.",
        {
            "version": 1,
            "citations": [],
            "integrity": _integrity(),
        },
        _policy(),
    )

    claims = result["quality"]["claims"]
    assert [claim["exact"] for claim in claims] == [
        "The company was founded in 1999.",
        "Alice is the CEO.",
    ]
    assert all(claim["citationRequired"] for claim in claims)
    assert all(claim["status"] == "unsupported" for claim in claims)
    assert result["quality"]["metrics"]["unsourcedClaimCount"] == 2


def test_extra_non_supporting_citation_does_not_degrade_supported_claim() -> None:
    def text_citation(citation_id: str, quote: str) -> dict:
        return {
            "citationId": citation_id,
            "source": {
                "sourceId": citation_id,
                "providerId": "documents",
                "sourceType": "document",
                "sourceCategory": "filings",
                "documentId": "doc-1",
                "title": "Annual report",
                "retrievedAt": "2026-07-30T10:00:00Z",
            },
            "evidence": {
                "kind": "text",
                "quote": quote,
                "snippet": "",
                "capturedAt": "2026-07-30T10:00:00Z",
            },
        }

    result = evaluate_citation_quality(
        "- Revenue grew [source](citation://cit_revenue)\n"
        "- Profit rose [source](citation://cit_revenue)"
        "[source](citation://cit_profit)",
        {
            "version": 1,
            "citations": [
                text_citation("cit_revenue", "Revenue grew from the filing."),
                text_citation("cit_profit", "Profit rose from the filing."),
            ],
            "integrity": _integrity(),
        },
        _policy(),
    )

    assert result["quality"]["status"] == "passed"
    assert result["quality"]["metrics"]["claimBoundCount"] == 2
    assert all(claim["status"] == "passed" for claim in result["quality"]["claims"])


def test_policy_rejects_real_structured_citation_with_wrong_field_semantics() -> None:
    citation = _structured()
    citation["evidence"]["field"] = "fiscal_year"
    citation["evidence"]["value"] = 2025
    citation["evidence"]["unit"] = "year"

    result = evaluate_citation_quality(
        "Revenue was 2025 USDm [source](citation://cit_revenue).",
        {
            "version": 1,
            "citations": [citation],
            "integrity": _integrity(),
        },
        _policy(),
    )

    claim = result["quality"]["claims"][0]
    assert claim["status"] == "unverified"
    assert claim["bindings"][0]["supportStatus"] == "not-found"
    assert "claim_evidence_mismatch" in claim["issueCodes"]
    assert "claim_evidence_mismatch" in {issue["code"] for issue in result["quality"]["issues"]}


def test_policy_recomputes_calculation_and_checks_input_provenance() -> None:
    left = _structured("cit_left")
    left["evidence"]["field"] = "current"
    left["evidence"]["value"] = 120
    right = _structured("cit_right")
    right["evidence"]["field"] = "prior"
    right["evidence"]["value"] = 100
    calculation = {
        "citationId": "cit_growth",
        "source": {
            "sourceId": "calculation-1",
            "providerId": "runtime",
            "sourceType": "tool-result",
            "title": "Growth calculation",
            "retrievedAt": "2026-07-30T10:00:00Z",
        },
        "evidence": {
            "kind": "calculation",
            "expression": "(current / prior) - 1",
            "inputs": [
                {
                    "name": "current",
                    "citationId": "cit_left",
                    "value": 120,
                    "unit": "USDm",
                },
                {
                    "name": "prior",
                    "citationId": "cit_right",
                    "value": 100,
                    "unit": "USDm",
                },
            ],
            "result": 0.25,
            "unit": "%",
            "rounding": "2dp",
            "calculatedAt": "2026-07-30T10:00:00Z",
        },
    }

    result = evaluate_citation_quality(
        "Values were 120 and 100; growth was 25%.",
        {
            "version": 1,
            "citations": [left, right, calculation],
            "integrity": _integrity(),
        },
        _policy(),
    )

    assert "calculation_result_mismatch" in {issue["code"] for issue in result["quality"]["issues"]}
    assert result["quality"]["layers"]["L4"] == "degraded"


def test_derived_claim_requires_calculation_evidence_not_only_input_citations() -> None:
    current = _structured("cit_current")
    current["evidence"]["field"] = "current_revenue"
    current["evidence"]["value"] = 120
    prior = _structured("cit_prior")
    prior["evidence"]["field"] = "prior_revenue"
    prior["evidence"]["value"] = 100

    result = evaluate_citation_quality(
        ("Revenue growth was 20% [current](citation://cit_current) [prior](citation://cit_prior)."),
        {
            "version": 1,
            "citations": [current, prior],
            "integrity": _integrity(),
        },
        _policy(),
    )

    assert "derived_claim_without_calculation_evidence" in {
        issue["code"] for issue in result["quality"]["issues"]
    }
    assert result["quality"]["publishStatus"] == "draft-only"


def test_low_tier_claim_without_primary_cross_check_is_unverified() -> None:
    news = {
        "citationId": "cit_news",
        "source": {
            "sourceId": "news-1",
            "providerId": "news",
            "sourceType": "web",
            "title": "News",
            "retrievedAt": "2026-07-30T10:00:00Z",
        },
        "evidence": {
            "kind": "text",
            "quote": "Management may cut guidance.",
            "snippet": "Management may cut guidance.",
            "capturedAt": "2026-07-30T10:00:00Z",
        },
        "annotations": {"provenance": {"toolName": "search.news_search"}},
    }

    result = evaluate_citation_quality(
        "Guidance may fall [news](citation://cit_news).",
        {
            "version": 1,
            "citations": [news],
            "integrity": _integrity(),
        },
        _policy(),
    )

    assert result["quality"]["status"] == "unverified"
    assert result["quality"]["issues"][0]["code"] == "low_tier_without_cross_check"
    assert result["citations"][0]["annotations"]["quality"]["status"] == "unverified"


def test_calculation_text_input_must_contain_the_claimed_value() -> None:
    text_input = {
        "citationId": "cit_text",
        "source": {
            "sourceId": "filing-1",
            "providerId": "documents",
            "sourceType": "document",
            "sourceCategory": "filings",
            "title": "Issuer filing",
            "retrievedAt": "2026-07-30T10:00:00Z",
        },
        "evidence": {
            "kind": "text",
            "quote": "Revenue increased during the period.",
            "snippet": "Revenue increased during the period.",
            "capturedAt": "2026-07-30T10:00:00Z",
        },
        "annotations": {"provenance": {"toolName": "search.filings_search"}},
    }
    calculation = {
        "citationId": "cit_calc",
        "source": {
            "sourceId": "calculation-1",
            "providerId": "runtime",
            "sourceType": "tool-result",
            "title": "Calculation",
            "retrievedAt": "2026-07-30T10:00:00Z",
        },
        "evidence": {
            "kind": "calculation",
            "expression": "revenue / 2",
            "inputs": [
                {
                    "name": "revenue",
                    "citationId": "cit_text",
                    "value": 120,
                    "unit": "USDm",
                }
            ],
            "result": 60,
            "unit": "USDm",
            "rounding": "0dp",
            "calculatedAt": "2026-07-30T10:00:00Z",
        },
    }

    result = evaluate_citation_quality(
        "Half-year revenue was 60 USDm [calc](citation://cit_calc).",
        {
            "version": 1,
            "citations": [text_input, calculation],
            "integrity": _integrity(),
        },
        _policy(),
    )

    assert "calculation_input_text_value_unverified" in {
        issue["code"] for issue in result["quality"]["issues"]
    }
    assert result["quality"]["publishStatus"] == "draft-only"


def test_document_category_tiers_fetched_chunk_not_generic_fetch_tool() -> None:
    filing = {
        "citationId": "cit_filing",
        "source": {
            "sourceId": "filing-1",
            "providerId": "valuz-search",
            "sourceType": "document",
            "sourceCategory": "filings",
            "title": "Issuer filing",
            "retrievedAt": "2026-07-30T10:00:00Z",
        },
        "evidence": {
            "kind": "text",
            "quote": "Revenue was 120.",
            "snippet": "Revenue was 120.",
            "capturedAt": "2026-07-30T10:00:00Z",
        },
        "annotations": {"provenance": {"toolName": "document_fetch"}},
    }

    result = evaluate_citation_quality(
        "Revenue was 120 [filing](citation://cit_filing).",
        {
            "version": 1,
            "citations": [filing],
            "integrity": _integrity(),
        },
        _policy(),
    )

    assert result["quality"]["status"] == "passed"
    assert result["quality"]["metrics"]["tierCounts"] == {"P2": 1}


def test_low_tier_requires_cross_check_on_same_claim_not_elsewhere() -> None:
    news = {
        "citationId": "cit_news",
        "source": {
            "sourceId": "news-1",
            "providerId": "news",
            "sourceType": "web",
            "title": "News",
            "retrievedAt": "2026-07-30T10:00:00Z",
        },
        "evidence": {
            "kind": "text",
            "quote": "Guidance may fall.",
            "snippet": "Guidance may fall.",
            "capturedAt": "2026-07-30T10:00:00Z",
        },
        "annotations": {"provenance": {"toolName": "search.news_search"}},
    }
    primary = _structured("cit_primary")

    result = evaluate_citation_quality(
        (
            "Guidance may fall [news](citation://cit_news). "
            "Revenue was 120 [data](citation://cit_primary)."
        ),
        {
            "version": 1,
            "citations": [news, primary],
            "integrity": _integrity(),
        },
        _policy(),
    )

    assert "low_tier_without_cross_check" in {
        issue["code"] for issue in result["quality"]["issues"]
    }

    checked = evaluate_citation_quality(
        ("Guidance may fall [news](citation://cit_news) [data](citation://cit_primary)."),
        {
            "version": 1,
            "citations": [news, primary],
            "integrity": _integrity(),
        },
        _policy(),
    )
    assert "low_tier_without_cross_check" not in {
        issue["code"] for issue in checked["quality"]["issues"]
    }


def test_quality_bundle_exposes_claim_audit_truncation() -> None:
    answer = "\n".join(
        f"- Company {index} reported revenue of {index + 1} USD."
        for index in range(MAX_CLAIMS_PER_ANSWER + 1)
    )

    result = evaluate_citation_quality(
        answer,
        {"version": 1, "citations": [], "integrity": _integrity()},
        _policy(),
    )

    assert result["quality"]["metrics"]["claimAuditTruncated"] is True
    assert "claim_audit_truncated" in {
        issue["code"] for issue in result["quality"]["issues"]
    }
