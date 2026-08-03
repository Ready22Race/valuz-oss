from __future__ import annotations

import json

from src.core.citation_repair import apply_citation_claim_patch


def _bundle() -> dict[str, object]:
    return {
        "quality": {
            "claims": [
                {
                    "claimId": "claim-revenue",
                    "location": {"sourceStart": 0, "sourceEnd": 11},
                },
                {
                    "claimId": "claim-profit",
                    "location": {"sourceStart": 12, "sourceEnd": 22},
                },
            ]
        }
    }


def _response(*patches: dict[str, object]) -> str:
    return json.dumps(
        {"version": "citation-claim-patch-v1", "patches": list(patches)},
        ensure_ascii=False,
    )


def test_claim_patch_changes_only_selected_claim_and_host_adds_link() -> None:
    baseline = "收入为 100 亿元。\n利润为 20 亿元。"
    result = apply_citation_claim_patch(
        baseline_text=baseline,
        baseline_bundle=_bundle(),
        response_text=_response(
            {
                "claimId": "claim-revenue",
                "replacementText": "收入为 120 亿元。",
                "evidenceHandles": ["ev_revenue_12345678"],
            }
        ),
        allowed_claim_ids={"claim-revenue"},
        allowed_evidence_handles={"ev_revenue_12345678"},
    )

    assert result.accepted is True
    assert result.text == (
        "收入为 120 亿元。 [1](evidence://ev_revenue_12345678)\n利润为 20 亿元。"
    )


def test_claim_patch_rejects_unknown_claim_or_handle() -> None:
    baseline = "收入为 100 亿元。\n利润为 20 亿元。"
    unknown_claim = apply_citation_claim_patch(
        baseline_text=baseline,
        baseline_bundle=_bundle(),
        response_text=_response(
            {
                "claimId": "claim-dashboard",
                "replacementText": "新增 Dashboard。",
                "evidenceHandles": [],
            }
        ),
        allowed_claim_ids={"claim-revenue"},
        allowed_evidence_handles=set(),
    )
    unknown_handle = apply_citation_claim_patch(
        baseline_text=baseline,
        baseline_bundle=_bundle(),
        response_text=_response(
            {
                "claimId": "claim-revenue",
                "replacementText": "收入为 120 亿元。",
                "evidenceHandles": ["ev_invented_12345678"],
            }
        ),
        allowed_claim_ids={"claim-revenue"},
        allowed_evidence_handles=set(),
    )

    assert unknown_claim.code == "claim-not-repairable"
    assert unknown_handle.code == "unknown-evidence-handle"


def test_claim_patch_rejects_whole_answer_or_empty_output() -> None:
    whole_answer = apply_citation_claim_patch(
        baseline_text="收入为 100 亿元。\n利润为 20 亿元。",
        baseline_bundle=_bundle(),
        response_text="重写后的完整回答",
        allowed_claim_ids={"claim-revenue"},
        allowed_evidence_handles=set(),
    )
    empty = apply_citation_claim_patch(
        baseline_text="收入为 100 亿元。\n利润为 20 亿元。",
        baseline_bundle=_bundle(),
        response_text=_response(),
        allowed_claim_ids={"claim-revenue"},
        allowed_evidence_handles=set(),
    )

    assert whole_answer.code == "invalid-json"
    assert empty.code == "empty-patch"


def test_claim_patch_cannot_replace_requested_field_with_proxy_metric() -> None:
    result = apply_citation_claim_patch(
        baseline_text="收入为 100 亿元。\n利润为 20 亿元。",
        baseline_bundle=_bundle(),
        response_text=_response(
            {
                "claimId": "claim-revenue",
                "replacementText": "毛利润为 120 亿元。",
                "evidenceHandles": ["ev_profit_12345678"],
            }
        ),
        allowed_claim_ids={"claim-revenue"},
        allowed_evidence_handles={"ev_profit_12345678"},
        required_fields_by_claim={"claim-revenue": ("收入",)},
    )

    assert result.accepted is False
    assert result.code == "requested-field-not-preserved"


def test_table_claim_patch_replaces_only_the_cell_source_span() -> None:
    baseline = "| 字段 | 数值 |\n|---|---|\n| 商誉 | 34.41亿元 |"
    start = baseline.index("34.41亿元")
    bundle = {
        "quality": {
            "claims": [
                {
                    "claimId": "claim-goodwill",
                    "location": {
                        "kind": "table-cell",
                        "sourceStart": start,
                        "sourceEnd": start + len("34.41亿元"),
                    },
                }
            ]
        }
    }
    result = apply_citation_claim_patch(
        baseline_text=baseline,
        baseline_bundle=bundle,
        response_text=_response(
            {
                "claimId": "claim-goodwill",
                "replacementText": "35.00亿元",
                "evidenceHandles": ["ev_goodwill_12345678"],
            }
        ),
        allowed_claim_ids={"claim-goodwill"},
        allowed_evidence_handles={"ev_goodwill_12345678"},
    )

    assert result.accepted is True
    assert result.text == (
        "| 字段 | 数值 |\n|---|---|\n| 商誉 | "
        "35.00亿元 [1](evidence://ev_goodwill_12345678) |"
    )
