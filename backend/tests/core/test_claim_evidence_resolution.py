from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
import src.core.claim_evidence_resolution as resolution_module
import yaml
from src.core.claim_audit import ClaimCandidate, extract_claims
from src.core.claim_evidence_resolution import (
    EvidenceCandidate,
    EvidenceCandidateIndex,
    SemanticVerificationResult,
    resolve_claim_evidence,
)

_FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "evaluation/fixtures/claim_evidence_resolution_cases.json"
)
_FIXTURE = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
_OSS_POLICY_PATH = (
    Path(__file__).resolve().parents[2]
    / "valuz_agent/resources/citation-policies/oss/policy.yaml"
)
_OSS_POLICY = yaml.safe_load(_OSS_POLICY_PATH.read_text(encoding="utf-8"))
_SEMANTICS = _OSS_POLICY["semantics"]


def _claim(case: dict[str, Any], index: int = 0) -> ClaimCandidate:
    raw = case["claim"]
    exact = str(raw["exact"])
    return ClaimCandidate(
        claim_id=str(case["resolver_case_id"]),
        exact=exact,
        segment_index=index,
        kind=str(raw.get("kind") or "factual-claim"),
        citation_required=bool(raw.get("citationRequired", True)),
        attached_citation_ids=(),
        normalized={str(key): str(value) for key, value in raw.get("normalized", {}).items()},
        location={"kind": "fixture", "blockIndex": index, "start": 0, "end": len(exact)},
        semantic_text=str(raw.get("semanticText") or exact),
        insertion_offset=len(exact),
        attached_evidence_handles=tuple(raw.get("explicitBindings") or ()),
    )


@pytest.mark.parametrize("case", _FIXTURE["cases"], ids=lambda case: case["resolver_case_id"])
def test_resolver_fixture(case: dict[str, Any]) -> None:
    resolution = resolve_claim_evidence(
        _claim(case),
        case.get("evidence_pool") or (),
        semantics=_SEMANTICS,
    )

    assert resolution.status == case["expected_status"]
    assert resolution.binding_action == case["expected_binding_action"]
    assert resolution.repair_action == case["expected_repair_action"]
    gold = set(case.get("gold_evidence_ids") or ())
    if gold:
        assert gold.intersection(resolution.candidate_handles[:5])


class _EntailingVerifier:
    def verify(
        self,
        claim: ClaimCandidate,
        candidates: tuple[EvidenceCandidate, ...],
    ) -> SemanticVerificationResult:
        assert claim.claim_id == "OSS-TEXT-002"
        assert len(candidates) <= 8
        return SemanticVerificationResult(
            verdict="entailed",
            evidence_handles=("ev_oss_doc_paraphrase",),
            confidence=0.98,
            verifier_revision="test-semantic-v1",
        )


def test_bounded_semantic_verifier_can_resolve_paraphrase() -> None:
    case = next(
        item
        for item in _FIXTURE["cases"]
        if item["resolver_case_id"] == "OSS-TEXT-002"
    )

    resolution = resolve_claim_evidence(
        _claim(case),
        case["evidence_pool"],
        semantics=_SEMANTICS,
        semantic_verifier=_EntailingVerifier(),
    )

    assert resolution.status == "verified"
    assert resolution.binding_action == "auto-bind"
    assert resolution.selected_handles == ("ev_oss_doc_paraphrase",)


def test_explicit_structured_binding_accepts_appended_period_metadata() -> None:
    semantics = copy.deepcopy(_SEMANTICS)
    semantics["metric_ontology"] = {
        "metrics": {
            "operating_revenue": {
                "aliases": ["营业收入"],
                "fields": ["operating_revenue"],
            },
            "reporting_period": {
                "aliases": ["财年", "报告期"],
                "fields": ["fiscal_year"],
            },
        }
    }
    handle = "ev_context_revenue_2024"
    claim = extract_claims(
        (
            "170,899,152,276，单位：人民币元（CNY），"
            "期间：2024 财年（截至 2024-12-31） "
            f"[1](evidence://{handle})"
        ),
        mode="strict-domain",
        semantics=semantics,
    )[0]
    evidence_pool = [
        {
            "evidenceHandle": handle,
            "source": {"providerId": "reportify", "sourceType": "dataset"},
            "evidence": {
                "kind": "structured-data",
                "field": "/data/0/total_revenue/operating_revenue",
                "metric": "operating_revenue",
                "value": 170899152276,
                "unit": "CNY",
                "period": "2024 annual",
                "asOf": "2024-12-31",
            },
        }
    ]

    resolution = resolve_claim_evidence(
        claim,
        evidence_pool,
        semantics=semantics,
    )

    assert "metric" not in claim.normalized
    assert resolution.status == "verified"
    assert resolution.binding_action == "keep"
    assert resolution.selected_handles == (handle,)


@pytest.mark.parametrize(
    "claim_text",
    (
        "数值：170,899,152,276 [1](evidence://ev_context_revenue_2024)",
        (
            "170,899,152,276 元（人民币），约 1,708.99 亿元 "
            "[1](evidence://ev_context_revenue_2024)"
        ),
    ),
)
def test_generic_value_and_unit_labels_do_not_create_false_metric_conflicts(
    claim_text: str,
) -> None:
    semantics = {
        "metric_ontology": {
            "metrics": {
                "operating_revenue": {
                    "aliases": ["营业收入"],
                    "fields": ["operating_revenue"],
                }
            }
        },
        "unit_ontology": {
            "units": {
                "yuan": {
                    "canonical": "CNY",
                    "aliases": ["元", "人民币元", "CNY"],
                    "scale": 1,
                },
                "hundred-million": {
                    "canonical": "CNY",
                    "aliases": ["亿元"],
                    "scale": 100_000_000,
                },
            }
        },
    }
    handle = "ev_context_revenue_2024"
    claim = extract_claims(
        claim_text,
        mode="strict-domain",
        semantics=semantics,
    )[0]
    resolution = resolve_claim_evidence(
        claim,
        [
            {
                "evidenceHandle": handle,
                "source": {"providerId": "reportify", "sourceType": "dataset"},
                "evidence": {
                    "kind": "structured-data",
                    "field": "/data/0/total_revenue/operating_revenue",
                    "metric": "operating_revenue",
                    "value": 170899152276,
                    "unit": "CNY",
                },
            }
        ],
        semantics=semantics,
    )

    assert "metric" not in claim.normalized
    assert resolution.status == "verified"
    assert resolution.selected_handles == (handle,)


def test_unresolved_claim_never_requests_repair() -> None:
    case = next(
        item
        for item in _FIXTURE["cases"]
        if item["resolver_case_id"] == "OSS-NEG-001"
    )

    resolution = resolve_claim_evidence(
        _claim(case),
        (),
        semantics=_SEMANTICS,
    )

    assert resolution.status == "unresolved"
    assert resolution.repair_action == "none"


def test_turn_local_index_bounds_large_registry_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = [
        {
            "evidenceHandle": f"ev_large_registry_{index:04d}",
            "source": {"providerId": "fixture"},
            "evidence": {
                "kind": "structured-data",
                "entityId": f"{index:06d}",
                "metric": "operating_revenue",
                "period": "2025 FY",
                "value": 100_000_000 + index,
                "unit": "CNY",
            },
        }
        for index in range(2_000)
    ]
    candidate_index = EvidenceCandidateIndex(records, semantics=_SEMANTICS)
    signal_calls = 0
    original_signals = resolution_module._candidate_signals

    def count_signals(*args: Any, **kwargs: Any) -> Any:
        nonlocal signal_calls
        signal_calls += 1
        return original_signals(*args, **kwargs)

    monkeypatch.setattr(resolution_module, "_candidate_signals", count_signals)
    for index in range(40):
        value = 100_000_000 + index
        claim = ClaimCandidate(
            claim_id=f"large-registry-{index}",
            exact=f"{index:06d} 2025 年营业收入为 {value} CNY。",
            segment_index=index,
            kind="structured-fact",
            citation_required=True,
            attached_citation_ids=(),
            normalized={
                "metric": "operating_revenue",
                "period": "2025 FY",
                "value": str(value),
                "unit": "CNY",
            },
            location={"kind": "fixture", "blockIndex": index, "start": 0, "end": 30},
            semantic_text=f"{index:06d} 2025 年营业收入为 {value} CNY。",
            insertion_offset=30,
            attached_evidence_handles=(),
        )

        resolution = resolve_claim_evidence(
            claim,
            candidate_index,
            semantics=_SEMANTICS,
        )

        assert resolution.status in {"verified", "supported-with-limits"}
        assert resolution.candidate_handles[0] == f"ev_large_registry_{index:04d}"

    # Forty claims against 2,000 records previously performed at least 80,000
    # expensive signal evaluations in each pipeline pass.  The turn-local
    # prefilter caps the work before deterministic verification.
    assert signal_calls <= 40 * candidate_index.prefilter_limit


def test_turn_local_entity_aliases_rebind_cross_company_source() -> None:
    claim = ClaimCandidate(
        claim_id="entity-alias-rebind",
        exact="闪迪 FY2026 Q3 营业收入为5,950,000,000 USD。",
        segment_index=0,
        kind="financial-fact",
        citation_required=True,
        attached_citation_ids=(),
        normalized={
            "metric": "operating_revenue",
            "period": "2026 Q3",
            "value": "5950000000",
            "unit": "USD",
        },
        location={"kind": "fixture", "blockIndex": 0, "start": 0, "end": 28},
        semantic_text="闪迪 FY2026 Q3 营业收入为5,950,000,000 USD。",
        insertion_offset=28,
        attached_evidence_handles=("ev_wrong_sk_doc",),
    )
    evidence_pool = [
        {
            "evidenceHandle": "ev_wrong_sk_doc",
            "source": {"title": "SK海力士(000660) - 2026 Q3 Quarterly Results"},
            "evidence": {
                "kind": "text",
                "quote": "FY2026 Q3 营业收入为5,950,000,000 USD。",
            },
        },
        {
            "evidenceHandle": "ev_sndk_revenue",
            "source": {"title": "Company income statement · SNDK"},
            "evidence": {
                "kind": "structured-data",
                "entityId": "SNDK",
                "metric": "operating_revenue",
                "period": "2026 Q3",
                "value": 5_950_000_000,
                "unit": "USD",
            },
        },
    ]

    resolution = resolve_claim_evidence(
        claim,
        evidence_pool,
        semantics=_SEMANTICS,
        entity_aliases={
            "闪迪": ("闪迪", "SNDK"),
            "SK海力士": ("SK海力士", "000660"),
        },
    )

    assert resolution.status == "verified"
    assert resolution.binding_action == "auto-rebind"
    assert resolution.selected_handles == ("ev_sndk_revenue",)
    assert resolution.support_by_handle["ev_wrong_sk_doc"] == "contradicted"


def test_added_distribution_ontology_does_not_weaken_unknown_base_metric() -> None:
    case = copy.deepcopy(
        next(
            item
            for item in _FIXTURE["cases"]
            if item["resolver_case_id"] == "OSS-BIND-001"
        )
    )
    case["claim"]["explicitBindings"] = []
    semantics = copy.deepcopy(_SEMANTICS)
    semantics["metric_ontology"] = {
        "metrics": {
            "finance_only_metric": {
                "aliases": ["finance only"],
                "fields": ["finance_only_metric"],
            }
        }
    }

    resolution = resolve_claim_evidence(
        _claim(case),
        case["evidence_pool"],
        semantics=semantics,
    )

    assert resolution.status == "verified"
    assert resolution.binding_action == "auto-bind"


def test_normalized_metric_matches_without_surface_label_or_ontology() -> None:
    claim = ClaimCandidate(
        claim_id="normalized-metric",
        exact="The 2025 value was 1 GW.",
        segment_index=0,
        kind="structured-fact",
        citation_required=True,
        attached_citation_ids=(),
        normalized={
            "metric": "available_capacity",
            "period": "2025 FY",
            "value": "1",
            "unit": "GW",
        },
        location={"kind": "fixture", "blockIndex": 0, "start": 0, "end": 24},
        semantic_text="The 2025 value was 1 GW.",
        insertion_offset=24,
        attached_evidence_handles=(),
    )

    resolution = resolve_claim_evidence(
        claim,
        [
            {
                "evidenceHandle": "ev_capacity",
                "source": {"providerId": "local-tool"},
                "evidence": {
                    "kind": "structured-data",
                    "metric": "available_capacity",
                    "period": "2025 FY",
                    "value": 1_000_000_000,
                    "unit": "W",
                },
            }
        ],
        semantics=_SEMANTICS,
    )

    assert resolution.status == "verified"
    assert resolution.binding_action == "auto-bind"


def test_metricless_calculation_input_auto_binds_by_unique_value_period_and_unit() -> None:
    claim = ClaimCandidate(
        claim_id="metricless-calculation-input",
        exact="2026 Q1: 10,285,128,726 CNY",
        segment_index=0,
        kind="structured-fact",
        citation_required=True,
        attached_citation_ids=(),
        normalized={
            "period": "2026 Q1",
            "value": "10285128726",
            "unit": "CNY",
        },
        location={"kind": "fixture", "blockIndex": 0, "start": 0, "end": 30},
        semantic_text="2026 Q1: 10,285,128,726 CNY",
        insertion_offset=30,
        attached_evidence_handles=(),
    )
    evidence_pool = [
        {
            "evidenceHandle": "ev_revenue_2026",
            "source": {"providerId": "financials"},
            "evidence": {
                "kind": "structured-data",
                "metric": "operating_revenue",
                "period": "2026 Q1",
                "value": 10_285_128_726,
                "unit": "CNY",
            },
        },
        {
            "evidenceHandle": "ev_revenue_2025",
            "source": {"providerId": "financials"},
            "evidence": {
                "kind": "structured-data",
                "metric": "operating_revenue",
                "period": "2025 Q1",
                "value": 10_445_537_525,
                "unit": "CNY",
            },
        },
    ]

    resolution = resolve_claim_evidence(claim, evidence_pool, semantics=_SEMANTICS)

    assert resolution.status == "verified"
    assert resolution.binding_action == "auto-bind"
    assert resolution.selected_handles == ("ev_revenue_2026",)


def test_metricless_value_period_match_stays_ambiguous_across_multiple_metrics() -> None:
    claim = ClaimCandidate(
        claim_id="metricless-ambiguous",
        exact="2026 Q1: 100 CNY",
        segment_index=0,
        kind="structured-fact",
        citation_required=True,
        attached_citation_ids=(),
        normalized={"period": "2026 Q1", "value": "100", "unit": "CNY"},
        location={"kind": "fixture", "blockIndex": 0, "start": 0, "end": 16},
        semantic_text="2026 Q1: 100 CNY",
        insertion_offset=16,
        attached_evidence_handles=(),
    )
    evidence_pool = [
        {
            "evidenceHandle": f"ev_{metric}",
            "source": {"providerId": "financials"},
            "evidence": {
                "kind": "structured-data",
                "metric": metric,
                "period": "2026 Q1",
                "value": 100,
                "unit": "CNY",
            },
        }
        for metric in ("revenue", "profit")
    ]

    resolution = resolve_claim_evidence(claim, evidence_pool, semantics=_SEMANTICS)

    assert resolution.status == "ambiguous"
    assert resolution.binding_action == "none"
    assert set(resolution.candidate_handles[:2]) == {"ev_revenue", "ev_profit"}


def test_short_unit_alias_does_not_consume_following_word() -> None:
    claim = ClaimCandidate(
        claim_id="short-unit-boundary",
        exact="Policy amount in 2024 was 2 W.",
        segment_index=0,
        kind="structured-fact",
        citation_required=True,
        attached_citation_ids=(),
        normalized={
            "metric": "available_capacity",
            "period": "2024 FY",
            "value": "2",
            "unit": "W",
        },
        location={"kind": "fixture", "blockIndex": 0, "start": 0, "end": 32},
        semantic_text="Policy amount in 2024 was 2 W.",
        insertion_offset=32,
        attached_evidence_handles=(),
    )
    resolution = resolve_claim_evidence(
        claim,
        [
            {
                "evidenceHandle": "ev_wrong_year_value",
                "source": {"providerId": "local-tool"},
                "evidence": {
                    "kind": "structured-data",
                    "metric": "available_capacity",
                    "period": "2024 FY",
                    "value": 2024,
                    "unit": "W",
                },
            }
        ],
        semantics=_SEMANTICS,
    )

    assert resolution.status == "unresolved"
    assert resolution.binding_action == "none"


def test_claim_and_evidence_dimension_aliases_are_canonicalized_symmetrically() -> None:
    claim = ClaimCandidate(
        claim_id="dimension-alias",
        exact="The consolidated 2024 amount was 123 USD.",
        segment_index=0,
        kind="structured-fact",
        citation_required=True,
        attached_citation_ids=(),
        normalized={
            "metric": "amount",
            "period": "2024 FY",
            "value": "123",
            "unit": "USD",
            "scope": "合并",
        },
        location={"kind": "fixture", "blockIndex": 0, "start": 0, "end": 43},
        semantic_text="The consolidated 2024 amount was 123 USD.",
        insertion_offset=43,
        attached_evidence_handles=(),
    )
    semantics = {
        "dimensions": {
            "scope": {"consolidated": ["consolidated", "合并"]},
        }
    }

    resolution = resolve_claim_evidence(
        claim,
        [
            {
                "evidenceHandle": "ev_dimension",
                "source": {"providerId": "controlled-data"},
                "evidence": {
                    "kind": "structured-data",
                    "metric": "amount",
                    "period": "2024 FY",
                    "value": 123,
                    "unit": "USD",
                    "scope": "consolidated",
                },
            }
        ],
        semantics=semantics,
    )

    assert resolution.status == "verified"
    assert resolution.binding_action == "auto-bind"


def test_missing_explicit_handle_is_invalid_instead_of_plain_unresolved() -> None:
    claim = ClaimCandidate(
        claim_id="missing-handle",
        exact="The service launched in June.",
        segment_index=0,
        kind="factual-claim",
        citation_required=True,
        attached_citation_ids=(),
        normalized={},
        location={"kind": "fixture", "blockIndex": 0, "start": 0, "end": 31},
        semantic_text="The service launched in June.",
        insertion_offset=31,
        attached_evidence_handles=("ev_missing",),
    )

    resolution = resolve_claim_evidence(claim, (), semantics=_SEMANTICS)

    assert resolution.status == "invalid-binding"
    assert resolution.binding_action == "none"
    assert resolution.reason_codes == ("explicit-binding-missing",)
