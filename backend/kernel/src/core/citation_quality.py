"""Generic evaluator for trusted declarative citation quality policies.

The evaluator intentionally knows nothing about Finance or any provider name.
An edition supplies tier matchers and rule switches as immutable session
metadata; this module evaluates canonical citations after the base guard has
finished and writes additive annotations.  It never changes citation ids,
source identity, evidence snapshots, or locators.
"""

from __future__ import annotations

import ast
import copy
import fnmatch
import re
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any

from src.core.claim_audit import (
    CLAIM_EXTRACTOR_REVISION,
    CLAIM_VERIFIER_REVISION,
    ClaimCandidate,
    canonical_evidence_dimension,
    canonical_evidence_metric,
    canonical_evidence_period,
    extract_claims_with_status,
    match_available_evidence,
    structured_components_cover_claim,
    structured_value_present,
    verify_evidence_support,
)

_UNSOURCED_RE = re.compile(r"\[UNSOURCED\]", re.IGNORECASE)
_UNVERIFIED_RE = re.compile(r"\[UNVERIFIED(?::[^\]]*)?\]", re.IGNORECASE)
_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_CITATION_LINK_RE = re.compile(r"\[[^\]\n]{0,240}\]\(citation://([A-Za-z0-9_-]{1,160})\)")
_CLAIM_BOUNDARY_RE = re.compile(r"(?<=[.!?。！？；;])\s+|\n+")
_FINANCIAL_NUMBER_RE = re.compile(
    r"(?<![\w])[-+]?\d[\d,]*(?:\.\d+)?"
    r"(?:\s*(?:%|bp|bps|[A-Z]{3}|百万元|亿元|万元|元|倍))",
    re.IGNORECASE,
)
_DERIVED_CLAIM_RE = re.compile(
    r"(?:同比|环比|复合增长|增长率|利润率|毛利率|净利率|占比|比率|回报率|"
    r"\bCAGR\b|\byoy\b|\bqoq\b|\byear[- ]over[- ]year\b|"
    r"\bquarter[- ]over[- ]quarter\b|\bgrowth(?: rate)?\b|"
    r"\bmargin\b|\bratio\b|\brate of change\b)",
    re.IGNORECASE,
)
_EXPLICIT_ARITHMETIC_RE = re.compile(
    r"(?:\d[\d,.]*|\))\s*(?:[+*/÷]|\s-\s)\s*(?:[-+]?\d|\()",
)
_ALLOWED_BINARY = {
    ast.Add: lambda left, right: left + right,
    ast.Sub: lambda left, right: left - right,
    ast.Mult: lambda left, right: left * right,
    ast.Div: lambda left, right: left / right,
}
_ALLOWED_UNARY = {
    ast.UAdd: lambda value: value,
    ast.USub: lambda value: -value,
}
_BASELINE_POLICY = {
    "policy_id": "oss-citation-baseline",
    "revision": "citation-baseline-v2",
    "mode": "required-on-evidence",
    "config": {
        "source_tiers": [],
        "rules": {"factual_claim": {"citation_required": True}},
        "failure": {"publish_on_degraded": "ready"},
    },
}


def evaluate_citation_quality(
    answer: str,
    bundle: dict[str, Any],
    policy_snapshot: dict[str, Any] | None,
    *,
    available_evidence: Any = (),
) -> dict[str, Any]:
    """Return a copy of *bundle* decorated with quality annotations."""

    if not isinstance(policy_snapshot, dict):
        policy_snapshot = _BASELINE_POLICY
    policy_id = _clean_text(policy_snapshot.get("policy_id"), "unknown")
    revision = _clean_text(policy_snapshot.get("revision"), "unknown")
    mode = _clean_text(policy_snapshot.get("mode"), "required-on-evidence")
    config = policy_snapshot.get("config")
    if not isinstance(config, dict):
        config = {"unavailable": True}

    result = copy.deepcopy(bundle)
    citations = result.get("citations")
    if not isinstance(citations, list):
        citations = []
        result["citations"] = citations
    issues: list[dict[str, Any]] = []
    layer_issues: dict[str, int] = defaultdict(int)
    claim_groups = _citation_claim_groups(answer)
    directly_linked_citation_ids = set(_CITATION_LINK_RE.findall(answer))

    def issue(
        code: str,
        layer: str,
        *,
        citation_ids: list[str] | None = None,
        claim: str | None = None,
        claim_id: str | None = None,
        location: dict[str, Any] | None = None,
        severity: str = "degraded",
    ) -> None:
        entry: dict[str, Any] = {
            "code": code,
            "layer": layer,
            "severity": severity,
        }
        if citation_ids:
            entry["citationIds"] = list(dict.fromkeys(citation_ids))
        if claim and claim.strip():
            entry["claim"] = {"exact": claim.strip()}
        if claim_id:
            entry["claimId"] = claim_id
        if location:
            entry["location"] = dict(location)
        issues.append(entry)
        layer_issues[layer] += 1

    integrity = result.get("integrity")
    if not isinstance(integrity, dict) or integrity.get("status") not in {
        "passed",
        "repaired",
    }:
        issue("base_integrity_not_passed", "L0")
    if config.get("unavailable") is True:
        issue("quality_policy_unavailable", "L0")

    tiers = config.get("source_tiers")
    tier_configs = (
        [item for item in tiers if isinstance(item, dict)] if isinstance(tiers, list) else []
    )
    tier_by_citation: dict[str, str | None] = {}
    authority_by_citation: dict[str, str | None] = {}
    citation_by_id: dict[str, dict[str, Any]] = {}
    for citation in citations:
        if not isinstance(citation, dict):
            issue("citation_invalid", "L0")
            continue
        citation_id = _clean_text(citation.get("citationId"), "")
        if not citation_id:
            issue("citation_id_missing", "L0")
            continue
        citation_by_id[citation_id] = citation
        tier = _match_tier(citation, tier_configs)
        tier_id = _clean_text(tier.get("id"), "") if tier else None
        authority = _clean_text(tier.get("authority"), "") if tier else None
        tier_by_citation[citation_id] = tier_id or None
        authority_by_citation[citation_id] = authority or None
        if tier_configs and not tier_id:
            issue("source_tier_unmatched", "L2", citation_ids=[citation_id])
        annotations = citation.get("annotations")
        if not isinstance(annotations, dict):
            annotations = {}
        annotations["quality"] = {
            "policyId": policy_id,
            "policyRevision": revision,
            "tier": tier_id,
            "authority": authority,
            "status": "pending",
            "label": tier_id,
        }
        citation["annotations"] = annotations

    rules = config.get("rules")
    rules = rules if isinstance(rules, dict) else {}
    numeric_rule = rules.get("numeric_claim")
    numeric_rule = numeric_rule if isinstance(numeric_rule, dict) else {}
    derived_rule = rules.get("derived_value")
    derived_rule = derived_rule if isinstance(derived_rule, dict) else {}
    time_rule = rules.get("time_boundary")
    time_rule = time_rule if isinstance(time_rule, dict) else {}
    semantics = config.get("semantics")
    semantics = semantics if isinstance(semantics, dict) else None

    structured_groups: dict[
        tuple[str, str, str, str],
        list[tuple[str, Any]],
    ] = defaultdict(list)
    cross_source_groups: dict[
        tuple[str, str, str],
        list[tuple[str, Any, str]],
    ] = defaultdict(list)
    for citation_id, citation in citation_by_id.items():
        evidence = citation.get("evidence")
        if not isinstance(evidence, dict):
            issue("evidence_invalid", "L1", citation_ids=[citation_id])
            continue
        kind = evidence.get("kind")
        if kind == "structured-data":
            structured_rule = numeric_rule
            if citation_id not in directly_linked_citation_ids:
                structured_rule = {
                    **numeric_rule,
                    "require_value_in_answer": False,
                }
            _validate_structured_evidence(
                _citation_context(answer, citation_id, claim_groups),
                citation_id,
                evidence,
                structured_rule,
                issue,
                semantics=semantics,
            )
            dataset_id = _clean_text(evidence.get("datasetId"), "")
            source = citation.get("source")
            source = source if isinstance(source, dict) else {}
            record_key = _clean_text(
                evidence.get("recordKey") or source.get("sourceId"),
                "",
            )
            field = _clean_text(evidence.get("field"), "")
            period = _clean_text(
                evidence.get("period") or evidence.get("asOf"),
                "",
            )
            structured_groups[(dataset_id, record_key, field, period)].append(
                (citation_id, evidence.get("value"))
            )
            subject = _structured_subject(citation, evidence)
            if subject and field and period:
                cross_source_groups[(subject, field, period)].append(
                    (
                        citation_id,
                        evidence.get("value"),
                        _structured_source_identity(citation, evidence),
                    )
                )
            _validate_time_boundary(
                _citation_context(answer, citation_id, claim_groups),
                citation_id,
                citation,
                evidence,
                time_rule,
                issue,
            )
        elif kind == "calculation":
            _validate_calculation(
                _citation_context(answer, citation_id, claim_groups),
                citation_id,
                evidence,
                citation_by_id,
                derived_rule,
                issue,
                semantics=semantics,
            )
        elif kind == "text":
            quote = evidence.get("quote")
            if not isinstance(quote, str) or not quote.strip():
                issue("text_quote_missing", "L1", citation_ids=[citation_id])
        else:
            issue("evidence_kind_unsupported", "L1", citation_ids=[citation_id])

    if derived_rule.get("require_calculation_evidence") is True:
        for claim_text, claim_ids in claim_groups:
            claim_citations = [
                citation_by_id[citation_id]
                for citation_id in claim_ids
                if citation_id in citation_by_id
            ]
            if any(
                isinstance(citation.get("evidence"), dict)
                and citation["evidence"].get("kind") == "calculation"
                for citation in claim_citations
            ):
                continue
            numeric_input_ids = [
                citation["citationId"]
                for citation in claim_citations
                if isinstance(citation.get("evidence"), dict)
                and citation["evidence"].get("kind") == "structured-data"
                and _as_decimal(citation["evidence"].get("value")) is not None
            ]
            if numeric_input_ids and _looks_like_derived_claim(
                claim_text,
                numeric_input_count=len(numeric_input_ids),
            ):
                issue(
                    "derived_claim_without_calculation_evidence",
                    "L4",
                    citation_ids=numeric_input_ids,
                )

    for values in structured_groups.values():
        normalized = {_stable_scalar(value) for _, value in values}
        if len(normalized) > 1:
            issue(
                "structured_source_conflict",
                "L3",
                citation_ids=[citation_id for citation_id, _ in values],
                severity="unverified",
            )

    conflict_rule = rules.get("conflicts")
    conflict_rule = conflict_rule if isinstance(conflict_rule, dict) else {}
    for values in cross_source_groups.values():
        source_identities = {source_identity for _, _, source_identity in values}
        normalized = {_stable_scalar(value) for _, value, _ in values}
        if len(source_identities) < 2 or len(normalized) < 2:
            continue
        citation_ids = [citation_id for citation_id, _, _ in values]
        issue(
            "cross_source_value_conflict",
            "L3",
            citation_ids=citation_ids,
            severity="unverified",
        )
        if conflict_rule.get("average_disallowed") is not True:
            continue
        numeric = [_as_decimal(value) for _, value, _ in values]
        if any(value is None for value in numeric):
            continue
        mean = sum(
            (value for value in numeric if value is not None),
            Decimal(0),
        ) / Decimal(len(numeric))
        for claim_text, claim_ids in claim_groups:
            if len(claim_ids.intersection(citation_ids)) < 2:
                continue
            if _value_present(mean, claim_text) and all(
                not _value_present(value, claim_text) for value in numeric if value is not None
            ):
                issue(
                    "conflicting_values_must_not_be_averaged",
                    "L4",
                    citation_ids=list(claim_ids.intersection(citation_ids)),
                    severity="unverified",
                )

    cross_rule = rules.get("low_tier_critical_claim")
    cross_rule = cross_rule if isinstance(cross_rule, dict) else {}
    if cross_rule.get("require_cross_check") is True:
        low_tiers = {
            str(value) for value in cross_rule.get("low_tiers", []) if isinstance(value, str)
        }
        check_tiers = {
            str(value)
            for value in cross_rule.get("cross_check_tiers", [])
            if isinstance(value, str)
        }
        low_ids = [
            citation_id for citation_id, tier in tier_by_citation.items() if tier in low_tiers
        ]
        low_without_check = [
            citation_id
            for citation_id in low_ids
            if not _citation_has_claim_cross_check(
                citation_id,
                claim_groups,
                tier_by_citation,
                check_tiers,
            )
        ]
        if low_without_check:
            issue(
                "low_tier_without_cross_check",
                "L3",
                citation_ids=low_without_check,
                severity="unverified",
            )

    claim_audits, claim_metrics = _audit_claims(
        answer,
        citation_by_id,
        list(available_evidence),
        mode=mode,
        enabled=(
            isinstance(rules.get("factual_claim"), dict)
            and rules["factual_claim"].get("citation_required") is True
        ),
        issue=issue,
        integrity=integrity,
        semantics=semantics,
    )
    unsourced_marker_count = len(_UNSOURCED_RE.findall(answer))
    unsourced_count = unsourced_marker_count + claim_metrics["unsupported"]
    unverified_count = len(_UNVERIFIED_RE.findall(answer)) + claim_metrics["unverified"]
    for claim in _claims_with_marker(answer, _UNSOURCED_RE):
        issue("answer_contains_unsourced_marker", "L5", claim=claim)
    for claim in _claims_with_marker(answer, _UNVERIFIED_RE):
        issue(
            "answer_contains_unverified_marker",
            "L5",
            claim=claim,
            severity="unverified",
        )

    _merge_issues_into_claim_audits(claim_audits, issues)

    issue_severity_by_citation: dict[str, set[str]] = defaultdict(set)
    for entry in issues:
        for citation_id in entry.get("citationIds", []):
            if isinstance(citation_id, str):
                issue_severity_by_citation[citation_id].add(
                    _clean_text(entry.get("severity"), "degraded")
                )
    for citation_id, citation in citation_by_id.items():
        quality = citation["annotations"]["quality"]
        severities = issue_severity_by_citation.get(citation_id, set())
        if not severities:
            quality["status"] = "passed"
        elif severities == {"unverified"}:
            quality["status"] = "unverified"
        else:
            quality["status"] = "degraded"

    status = "passed"
    if issues:
        status = (
            "unverified"
            if all(entry.get("severity") == "unverified" for entry in issues)
            else "degraded"
        )
    publish_status = "ready"
    failure = config.get("failure")
    failure = failure if isinstance(failure, dict) else {}
    if issues and failure.get("publish_on_degraded", "draft_only") == "draft_only":
        publish_status = "draft-only"

    tier_counts = Counter(
        tier for tier in tier_by_citation.values() if isinstance(tier, str) and tier
    )
    result["quality"] = {
        "policyId": policy_id,
        "policyRevision": revision,
        "mode": mode,
        "status": status,
        "publishStatus": publish_status,
        "layers": {
            layer: "degraded" if layer_issues.get(layer) else "passed"
            for layer in ("L0", "L1", "L2", "L3", "L4", "L5")
        },
        "issues": issues,
        "claims": claim_audits,
        "extractorRevision": CLAIM_EXTRACTOR_REVISION,
        "verifierRevision": CLAIM_VERIFIER_REVISION,
        "metrics": {
            "citationCount": len(citation_by_id),
            "claimDetectedCount": claim_metrics["detected"],
            "claimCitationRequiredCount": claim_metrics["required"],
            "claimBoundCount": claim_metrics["bound"],
            "claimAutoBoundCount": claim_metrics["auto_bound"],
            "claimUnsupportedCount": claim_metrics["unsupported"],
            "claimSemanticMismatchCount": claim_metrics["mismatch"],
            "claimAmbiguousCount": claim_metrics["ambiguous"],
            "claimAuditTruncated": bool(claim_metrics["truncated"]),
            "unsourcedClaimCount": unsourced_count,
            "unverifiedClaimCount": unverified_count,
            "tierCounts": dict(sorted(tier_counts.items())),
        },
    }
    return result


def _audit_claims(
    answer: str,
    citation_by_id: dict[str, dict[str, Any]],
    available_evidence: list[Any],
    *,
    mode: str,
    enabled: bool,
    issue: Any,
    integrity: dict[str, Any],
    semantics: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    claims, extraction_truncated = extract_claims_with_status(
        answer,
        mode=mode,
        semantics=semantics,
    )
    audits: list[dict[str, Any]] = []
    metrics = {
        "detected": len(claims),
        "required": 0,
        "bound": 0,
        "auto_bound": 0,
        "unsupported": 0,
        "unverified": 0,
        "mismatch": 0,
        "ambiguous": 0,
        "truncated": int(extraction_truncated),
    }
    if extraction_truncated:
        issue("claim_audit_truncated", "L0")
    for claim in claims:
        required = claim.citation_required if enabled else bool(claim.attached_citation_ids)
        if required:
            metrics["required"] += 1
        citation_ids = [
            citation_id
            for citation_id in claim.attached_citation_ids
            if citation_id in citation_by_id
        ]
        issue_codes: list[str] = []
        bindings: list[dict[str, str]] = []
        status = "passed"
        auto_bound = _claim_was_auto_bound(claim, citation_ids, citation_by_id)
        if not required:
            audits.append(
                claim.to_bundle_dict(
                    citation_ids=citation_ids,
                    status="passed",
                )
            )
            continue
        if not citation_ids:
            match = match_available_evidence(
                claim,
                available_evidence,
                semantics=semantics,
            )
            if match.status == "ambiguous":
                code = "claim_evidence_ambiguous"
                status = "unverified"
                metrics["ambiguous"] += 1
                metrics["unverified"] += 1
                severity = "unverified"
            elif match.status == "conflict":
                code = "claim_evidence_conflict"
                status = "unverified"
                metrics["unverified"] += 1
                severity = "unverified"
            else:
                code = _missing_claim_code(claim)
                status = "unsupported"
                metrics["unsupported"] += 1
                severity = "degraded"
            issue_codes.append(code)
            issue(
                code,
                "L4",
                claim=claim.exact,
                claim_id=claim.claim_id,
                location=claim.location,
                severity=severity,
            )
        else:
            metrics["bound"] += 1
            support_rows: list[tuple[str, str, int]] = []
            for citation_id in citation_ids:
                citation = citation_by_id[citation_id]
                support = verify_evidence_support(
                    claim,
                    citation,
                    semantics=semantics,
                )
                support_rows.append((citation_id, support.status, support.directness))
            primary_id = _select_primary_citation(support_rows, citation_by_id)
            for citation_id, support_status, _directness in support_rows:
                if support_status == "supported":
                    role = "primary" if citation_id == primary_id else "corroborating"
                elif support_status == "partially-supported":
                    role = "component"
                elif support_status == "contradicted":
                    role = "conflicting"
                else:
                    role = "component"
                bindings.append(
                    {
                        "citationId": citation_id,
                        "role": role,
                        "supportStatus": support_status,
                    }
                )
            bindings.extend(
                _calculation_input_bindings(
                    citation_ids,
                    citation_by_id,
                )
            )
            supported = [row for row in support_rows if row[1] == "supported"]
            partial = [row for row in support_rows if row[1] == "partially-supported"]
            contradicted = [row for row in support_rows if row[1] == "contradicted"]
            missing = [row for row in support_rows if row[1] == "not-found"]
            component_covered = structured_components_cover_claim(
                claim,
                [citation_by_id[citation_id] for citation_id in citation_ids],
                semantics=semantics,
            )
            if contradicted or (not supported and not partial and not component_covered):
                code = "claim_evidence_mismatch"
                issue_codes.append(code)
                status = "unverified"
                metrics["mismatch"] += 1
                metrics["unverified"] += 1
                issue(
                    code,
                    "L4",
                    citation_ids=[row[0] for row in contradicted + missing],
                    claim=claim.exact,
                    claim_id=claim.claim_id,
                    location=claim.location,
                    severity="unverified",
                )
            elif not supported and (partial or missing) and not component_covered:
                code = "claim_partially_supported"
                issue_codes.append(code)
                status = "unverified"
                metrics["unverified"] += 1
                issue(
                    code,
                    "L4",
                    citation_ids=[row[0] for row in partial + missing],
                    claim=claim.exact,
                    claim_id=claim.claim_id,
                    location=claim.location,
                    severity="unverified",
                )
            elif auto_bound:
                status = "auto-bound"
                metrics["auto_bound"] += 1
            elif integrity.get("status") == "repaired":
                status = "repaired"
        audits.append(
            claim.to_bundle_dict(
                citation_ids=citation_ids,
                bindings=bindings,
                status=status,
                issue_codes=issue_codes,
            )
        )
    return audits, metrics


def _missing_claim_code(claim: ClaimCandidate) -> str:
    if claim.kind in {"financial-fact", "numeric-fact", "calculation"}:
        return "numeric_claim_without_citation"
    if claim.kind == "date-fact":
        return "date_claim_without_citation"
    return "claim_without_citation"


def _claim_was_auto_bound(
    claim: ClaimCandidate,
    citation_ids: list[str],
    citation_by_id: dict[str, dict[str, Any]],
) -> bool:
    for citation_id in citation_ids:
        annotations = citation_by_id[citation_id].get("annotations")
        annotations = annotations if isinstance(annotations, dict) else {}
        binding = annotations.get("binding")
        binding = binding if isinstance(binding, dict) else {}
        claim_ids = binding.get("autoBoundClaimIds")
        if isinstance(claim_ids, list) and claim.claim_id in claim_ids:
            return True
    return False


def _calculation_input_bindings(
    direct_citation_ids: list[str],
    citation_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, str]]:
    """Expose calculation dependencies without treating them as inline claims."""

    output: list[dict[str, str]] = []
    seen = set(direct_citation_ids)
    for citation_id in direct_citation_ids:
        citation = citation_by_id.get(citation_id)
        evidence = citation.get("evidence") if isinstance(citation, dict) else None
        if not isinstance(evidence, dict) or evidence.get("kind") != "calculation":
            continue
        inputs = evidence.get("inputs")
        if not isinstance(inputs, list):
            continue
        for item in inputs:
            if not isinstance(item, dict):
                continue
            dependency_id = item.get("citationId")
            if not isinstance(dependency_id, str) or dependency_id in seen:
                continue
            dependency = citation_by_id.get(dependency_id)
            dependency_evidence = (
                dependency.get("evidence") if isinstance(dependency, dict) else None
            )
            support_status = "not-found"
            if isinstance(dependency_evidence, dict):
                kind = dependency_evidence.get("kind")
                if kind == "structured-data":
                    value_matches = _stable_scalar(
                        dependency_evidence.get("value")
                    ) == _stable_scalar(item.get("value"))
                    cited_unit = _clean_text(dependency_evidence.get("unit"), "")
                    input_unit = _clean_text(item.get("unit"), "")
                    unit_matches = not (cited_unit or input_unit) or cited_unit == input_unit
                    support_status = (
                        "supported" if value_matches and unit_matches else "contradicted"
                    )
                elif kind == "text":
                    quote = _clean_text(dependency_evidence.get("quote"), "")
                    support_status = (
                        "supported"
                        if quote and _value_present(item.get("value"), quote)
                        else "not-found"
                    )
            output.append(
                {
                    "citationId": dependency_id,
                    "role": "calculation-input",
                    "supportStatus": support_status,
                }
            )
            seen.add(dependency_id)
    return output


def _select_primary_citation(
    support_rows: list[tuple[str, str, int]],
    citation_by_id: dict[str, dict[str, Any]],
) -> str | None:
    supported = [row for row in support_rows if row[1] == "supported"]
    if not supported:
        return None

    def key(row: tuple[str, str, int]) -> tuple[int, int, int, str]:
        citation_id, _status, directness = row
        citation = citation_by_id[citation_id]
        annotations = citation.get("annotations")
        annotations = annotations if isinstance(annotations, dict) else {}
        quality = annotations.get("quality")
        quality = quality if isinstance(quality, dict) else {}
        authority = _clean_text(quality.get("authority"), "")
        authority_rank = {
            "primary": 4,
            "issuer": 3,
            "authoritative": 3,
            "secondary": 2,
        }.get(authority, 0)
        locator_rank = 1 if isinstance(citation.get("locator"), dict) else 0
        return (-directness, -authority_rank, -locator_rank, citation_id)

    return min(supported, key=key)[0]


def _merge_issues_into_claim_audits(
    audits: list[dict[str, Any]],
    issues: list[dict[str, Any]],
) -> None:
    by_id = {
        audit.get("claimId"): audit for audit in audits if isinstance(audit.get("claimId"), str)
    }
    for issue_entry in issues:
        targets: list[dict[str, Any]] = []
        claim_id = issue_entry.get("claimId")
        if isinstance(claim_id, str) and claim_id in by_id:
            targets = [by_id[claim_id]]
        elif isinstance(issue_entry.get("citationIds"), list):
            citation_ids = {value for value in issue_entry["citationIds"] if isinstance(value, str)}
            targets = [
                audit
                for audit in audits
                if citation_ids.intersection(audit.get("citationIds") or [])
            ]
        for audit in targets:
            codes = audit.setdefault("issueCodes", [])
            code = issue_entry.get("code")
            if isinstance(code, str) and code not in codes:
                codes.append(code)
            if audit.get("status") in {"passed", "auto-bound", "repaired"}:
                audit["status"] = (
                    "unverified" if issue_entry.get("severity") == "unverified" else "degraded"
                )


def _match_tier(
    citation: dict[str, Any],
    tiers: list[dict[str, Any]],
) -> dict[str, Any] | None:
    source = citation.get("source")
    source = source if isinstance(source, dict) else {}
    evidence = citation.get("evidence")
    evidence = evidence if isinstance(evidence, dict) else {}
    annotations = citation.get("annotations")
    annotations = annotations if isinstance(annotations, dict) else {}
    provenance = annotations.get("provenance")
    provenance = provenance if isinstance(provenance, dict) else {}
    tool_name = _clean_text(
        evidence.get("toolName") or provenance.get("toolName"),
        "",
    )
    provider_id = _clean_text(source.get("providerId"), "")
    source_type = _clean_text(source.get("sourceType"), "")
    source_category = _clean_text(source.get("sourceCategory"), "")
    for tier in tiers:
        match = tier.get("match")
        if not isinstance(match, dict):
            continue
        alternatives = match.get("any")
        if isinstance(alternatives, list):
            if any(
                _matches_source(
                    candidate,
                    source_type=source_type,
                    source_category=source_category,
                    tool_name=tool_name,
                    provider_id=provider_id,
                )
                for candidate in alternatives
                if isinstance(candidate, dict)
            ):
                return tier
            continue
        if _matches_source(
            match,
            source_type=source_type,
            source_category=source_category,
            tool_name=tool_name,
            provider_id=provider_id,
        ):
            return tier
    return None


def _matches_source(
    match: dict[str, Any],
    *,
    source_type: str,
    source_category: str,
    tool_name: str,
    provider_id: str,
) -> bool:
    source_types = _string_list(match.get("source_types"))
    source_categories = _string_list(match.get("source_categories"))
    tools = _string_list(match.get("tools"))
    providers = _string_list(match.get("providers"))
    if source_types and source_type not in source_types:
        return False
    if source_categories and source_category not in source_categories:
        return False
    if tools and not any(fnmatch.fnmatchcase(tool_name, pattern) for pattern in tools):
        return False
    if providers and not any(fnmatch.fnmatchcase(provider_id, pattern) for pattern in providers):
        return False
    return bool(source_types or source_categories or tools or providers)


def _structured_subject(
    citation: dict[str, Any],
    evidence: dict[str, Any],
) -> str | None:
    """Best-effort issuer/instrument key without conflating data providers."""

    source = citation.get("source")
    source = source if isinstance(source, dict) else {}
    dataset_id = _clean_text(evidence.get("datasetId"), "")
    for raw in (source.get("sourceId"), evidence.get("recordKey")):
        value = _clean_text(raw, "")
        if not value:
            continue
        if dataset_id and value.startswith(f"{dataset_id}:"):
            value = value[len(dataset_id) + 1 :]
        subject = value.split("|", 1)[0].strip()
        if subject and subject != dataset_id:
            return subject
    return None


def _structured_source_identity(
    citation: dict[str, Any],
    evidence: dict[str, Any],
) -> str:
    source = citation.get("source")
    source = source if isinstance(source, dict) else {}
    return "\0".join(
        (
            _clean_text(source.get("providerId"), ""),
            _clean_text(evidence.get("datasetId"), ""),
            _clean_text(source.get("sourceId"), ""),
        )
    )


def _validate_structured_evidence(
    claim_text: str,
    citation_id: str,
    evidence: dict[str, Any],
    rule: dict[str, Any],
    issue: Any,
    *,
    semantics: dict[str, Any] | None,
) -> None:
    for field in ("datasetId", "toolName", "field", "capturedAt"):
        if not _clean_text(evidence.get(field), ""):
            issue(
                f"structured_{_snake(field)}_missing",
                "L1",
                citation_ids=[citation_id],
            )
    value = evidence.get("value")
    if value is None or isinstance(value, (dict, list)):
        issue("structured_value_missing", "L1", citation_ids=[citation_id])
        return
    numeric = _as_decimal(value) is not None
    if (
        numeric
        and rule.get("require_unit") is True
        and not _clean_text(
            evidence.get("unit"),
            "",
        )
    ):
        issue("numeric_unit_missing", "L1", citation_ids=[citation_id])
    if (
        numeric
        and rule.get("require_period_or_as_of") is True
        and not (_clean_text(evidence.get("period"), "") or _clean_text(evidence.get("asOf"), ""))
    ):
        issue("numeric_period_or_as_of_missing", "L1", citation_ids=[citation_id])
    if numeric and rule.get("require_value_in_answer") is True:
        if not structured_value_present(
            value,
            _clean_text(evidence.get("unit"), ""),
            claim_text,
            semantics=semantics,
        ):
            issue(
                "structured_value_not_present_in_answer",
                "L4",
                citation_ids=[citation_id],
            )


def _validate_calculation(
    claim_text: str,
    citation_id: str,
    evidence: dict[str, Any],
    citation_by_id: dict[str, dict[str, Any]],
    rule: dict[str, Any],
    issue: Any,
    *,
    semantics: dict[str, Any] | None = None,
) -> None:
    expression = evidence.get("expression")
    inputs = evidence.get("inputs")
    if not isinstance(expression, str) or not expression.strip():
        issue("calculation_expression_missing", "L4", citation_ids=[citation_id])
        return
    if not isinstance(inputs, list) or not inputs:
        issue("calculation_inputs_missing", "L4", citation_ids=[citation_id])
        return
    variables: dict[str, Decimal] = {}
    input_units: list[str] = []
    for item in inputs:
        if not isinstance(item, dict):
            issue("calculation_input_invalid", "L4", citation_ids=[citation_id])
            continue
        name = item.get("name")
        input_citation_id = item.get("citationId")
        if not isinstance(name, str) or not name.isidentifier():
            issue("calculation_input_name_invalid", "L4", citation_ids=[citation_id])
            continue
        if not isinstance(input_citation_id, str) or input_citation_id not in citation_by_id:
            issue(
                "calculation_input_citation_missing",
                "L4",
                citation_ids=[citation_id],
            )
            continue
        input_citation = citation_by_id[input_citation_id]
        input_evidence = input_citation.get("evidence")
        if isinstance(input_evidence, dict):
            input_kind = input_evidence.get("kind")
            if input_kind == "structured-data":
                if _stable_scalar(input_evidence.get("value")) != _stable_scalar(item.get("value")):
                    issue(
                        "calculation_input_value_mismatch",
                        "L4",
                        citation_ids=[citation_id, input_citation_id],
                    )
                cited_unit = _clean_text(input_evidence.get("unit"), "")
                input_unit = _clean_text(item.get("unit"), "")
                if (cited_unit or input_unit) and cited_unit != input_unit:
                    issue(
                        "calculation_input_unit_mismatch",
                        "L4",
                        citation_ids=[citation_id, input_citation_id],
                    )
                _validate_calculation_input_semantics(
                    calculation=evidence,
                    input_name=name,
                    input_evidence=input_evidence,
                    calculation_citation_id=citation_id,
                    input_citation_id=input_citation_id,
                    semantics=semantics,
                    issue=issue,
                )
            elif input_kind == "text":
                quote = _clean_text(input_evidence.get("quote"), "")
                if not quote or not _value_present(item.get("value"), quote):
                    issue(
                        "calculation_input_text_value_unverified",
                        "L4",
                        citation_ids=[citation_id, input_citation_id],
                    )
            else:
                issue(
                    "calculation_input_evidence_unsupported",
                    "L4",
                    citation_ids=[citation_id, input_citation_id],
                )
        try:
            variables[name] = Decimal(str(item.get("value")))
        except (InvalidOperation, ValueError):
            issue("calculation_input_value_invalid", "L4", citation_ids=[citation_id])
        unit = item.get("unit")
        if isinstance(unit, str) and unit:
            input_units.append(unit)
    try:
        calculated = _safe_decimal_eval(expression, variables)
        expected = Decimal(str(evidence.get("result")))
    except (InvalidOperation, ValueError, ZeroDivisionError):
        issue("calculation_expression_unsupported", "L4", citation_ids=[citation_id])
        return
    tolerance = _rounding_tolerance(evidence.get("rounding"))
    if not calculated.is_finite() or abs(calculated - expected) > tolerance:
        issue("calculation_result_mismatch", "L4", citation_ids=[citation_id])
    if rule.get("require_unit") is True and not _clean_text(evidence.get("unit"), ""):
        issue("calculation_unit_missing", "L4", citation_ids=[citation_id])
    if rule.get("require_result_in_answer") is True and not _value_present(
        evidence.get("result"),
        claim_text,
    ):
        issue(
            "calculation_result_not_present_in_answer",
            "L4",
            citation_ids=[citation_id],
        )
    if (
        rule.get("require_compatible_units") is True
        and _expression_has_additive_op(expression)
        and len(set(input_units)) > 1
    ):
        issue("calculation_input_unit_mismatch", "L4", citation_ids=[citation_id])


def _validate_calculation_input_semantics(
    *,
    calculation: dict[str, Any],
    input_name: str,
    input_evidence: dict[str, Any],
    calculation_citation_id: str,
    input_citation_id: str,
    semantics: dict[str, Any] | None,
    issue: Any,
) -> None:
    if not isinstance(semantics, dict):
        return
    citation_ids = [calculation_citation_id, input_citation_id]
    calculation_metric = canonical_evidence_metric(calculation, semantics)
    dependencies = semantics.get("calculation_dependencies")
    dependencies = dependencies if isinstance(dependencies, dict) else {}
    allowed_metrics = dependencies.get(calculation_metric)
    if isinstance(allowed_metrics, list) and allowed_metrics:
        input_metric = canonical_evidence_metric(input_evidence, semantics)
        allowed = {str(value) for value in allowed_metrics if str(value)}
        if input_metric not in allowed:
            issue(
                "calculation_input_metric_mismatch",
                "L4",
                citation_ids=citation_ids,
            )

    calculation_entity = _clean_text(
        calculation.get("entityId") or calculation.get("entityName"),
        "",
    )
    input_entity = _clean_text(
        input_evidence.get("entityId") or input_evidence.get("entityName"),
        "",
    )
    if calculation_entity and input_entity and calculation_entity != input_entity:
        issue(
            "calculation_input_entity_mismatch",
            "L4",
            citation_ids=citation_ids,
        )

    for dimension in ("scope", "basis"):
        calculation_value = _clean_text(calculation.get(dimension), "")
        input_value = _clean_text(input_evidence.get(dimension), "")
        if not calculation_value:
            continue
        if not input_value or canonical_evidence_dimension(
            input_value,
            semantics,
            dimension,
        ) != canonical_evidence_dimension(calculation_value, semantics, dimension):
            issue(
                f"calculation_input_{dimension}_mismatch",
                "L4",
                citation_ids=citation_ids,
            )

    calculation_period = canonical_evidence_period(
        _clean_text(calculation.get("period"), ""),
        semantics,
    )
    if not calculation_period or input_name not in {"current", "prior"}:
        return
    input_period = canonical_evidence_period(
        _clean_text(
            input_evidence.get("period") or input_evidence.get("asOf"),
            "",
        ),
        semantics,
    )
    expected_period = (
        calculation_period
        if input_name == "current"
        else _previous_comparable_period(calculation_period)
    )
    if not input_period or not expected_period or input_period != expected_period:
        issue(
            "calculation_input_period_mismatch",
            "L4",
            citation_ids=citation_ids,
        )


def _previous_comparable_period(period: str) -> str:
    match = re.fullmatch(r"((?:19|20)\d{2})(.*)", period)
    if match is None:
        return ""
    return f"{int(match.group(1)) - 1}{match.group(2)}"


def _validate_time_boundary(
    claim_text: str,
    citation_id: str,
    citation: dict[str, Any],
    evidence: dict[str, Any],
    rule: dict[str, Any],
    issue: Any,
) -> None:
    if rule.get("forbid_extrapolation") is not True:
        return
    annotations = citation.get("annotations")
    annotations = annotations if isinstance(annotations, dict) else {}
    provenance = annotations.get("provenance")
    provenance = provenance if isinstance(provenance, dict) else {}
    coverage = provenance.get("coverage")
    if not isinstance(coverage, dict):
        coverage = evidence.get("coverage")
    coverage = coverage if isinstance(coverage, dict) else {}
    as_of = _date_prefix(evidence.get("asOf"))
    start = _date_prefix(coverage.get("start"))
    end = _date_prefix(coverage.get("end"))
    if rule.get("require_coverage") is True and as_of and not (start or end):
        issue("evidence_coverage_missing", "L5", citation_ids=[citation_id])
    if as_of and start and as_of < start:
        issue("evidence_before_coverage", "L5", citation_ids=[citation_id])
    if as_of and end and as_of > end:
        issue("evidence_after_coverage", "L5", citation_ids=[citation_id])
    claim_dates = _ISO_DATE_RE.findall(claim_text)
    if start and any(value < start for value in claim_dates):
        issue(
            "claim_before_evidence_coverage",
            "L5",
            citation_ids=[citation_id],
            severity="unverified",
        )
    if end and any(value > end for value in claim_dates):
        issue(
            "claim_after_evidence_coverage",
            "L5",
            citation_ids=[citation_id],
            severity="unverified",
        )


def _safe_decimal_eval(expression: str, values: dict[str, Decimal]) -> Decimal:
    if len(expression) > 500:
        raise ValueError("expression_too_long")
    root = ast.parse(expression, mode="eval")

    def evaluate(node: ast.AST, depth: int = 0) -> Decimal:
        if depth > 32:
            raise ValueError("expression_too_deep")
        if isinstance(node, ast.Expression):
            return evaluate(node.body, depth + 1)
        if isinstance(node, ast.Name) and node.id in values:
            return values[node.id]
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return Decimal(str(node.value))
        if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINARY:
            return _ALLOWED_BINARY[type(node.op)](
                evaluate(node.left, depth + 1),
                evaluate(node.right, depth + 1),
            )
        if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARY:
            return _ALLOWED_UNARY[type(node.op)](evaluate(node.operand, depth + 1))
        raise ValueError("unsupported_expression")

    return evaluate(root)


def _expression_has_additive_op(expression: str) -> bool:
    try:
        root = ast.parse(expression, mode="eval")
    except SyntaxError:
        return False
    return any(
        isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub))
        for node in ast.walk(root)
    )


def _rounding_tolerance(value: Any) -> Decimal:
    if isinstance(value, str):
        match = re.fullmatch(r"\s*(\d{1,6})\s*dp\s*", value, re.IGNORECASE)
        if match:
            return Decimal("0.5") * (Decimal(10) ** -int(match.group(1)))
        try:
            step = abs(Decimal(value))
            if step:
                return step / 2
        except InvalidOperation:
            pass
    return Decimal("0.000001")


def _string_list(value: Any) -> list[str]:
    return [str(item) for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _clean_text(value: Any, default: str) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else default


def _stable_scalar(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _as_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _value_present(value: Any, text: str) -> bool:
    if not text:
        return False
    literal = _stable_scalar(value)
    candidates = {literal, literal.replace(",", "")}
    decimal = _as_decimal(value)
    if decimal is not None:
        normalized = format(decimal, "f")
        candidates.add(normalized)
        if "." in normalized:
            candidates.add(normalized.rstrip("0").rstrip("."))
        integer = int(decimal)
        if decimal == integer:
            candidates.add(f"{integer:,}")
    return any(candidate and candidate in text for candidate in candidates)


def _citation_claim_groups(answer: str) -> list[tuple[str, set[str]]]:
    groups: list[tuple[str, set[str]]] = []
    for segment in _CLAIM_BOUNDARY_RE.split(answer):
        citation_ids = set(_CITATION_LINK_RE.findall(segment))
        if citation_ids:
            groups.append((segment, citation_ids))
    return groups


def _uncited_numeric_claims(answer: str) -> list[str]:
    claims: list[str] = []
    for segment in _CLAIM_BOUNDARY_RE.split(answer):
        if (
            _FINANCIAL_NUMBER_RE.search(segment)
            and not _CITATION_LINK_RE.search(segment)
            and not _UNSOURCED_RE.search(segment)
        ):
            claims.append(segment)
    return claims


def _claims_with_marker(answer: str, marker: re.Pattern[str]) -> list[str]:
    return [segment for segment in _CLAIM_BOUNDARY_RE.split(answer) if marker.search(segment)]


def _citation_context(
    answer: str,
    citation_id: str,
    groups: list[tuple[str, set[str]]],
) -> str:
    matches = [text for text, ids in groups if citation_id in ids]
    return "\n".join(matches) if matches else answer


def _looks_like_derived_claim(
    claim_text: str,
    *,
    numeric_input_count: int,
) -> bool:
    # Strip internal citation URIs before looking for arithmetic; otherwise
    # the slashes in ``citation://`` would make every cited claim look derived.
    plain = _CITATION_LINK_RE.sub("", claim_text)
    if _EXPLICIT_ARITHMETIC_RE.search(plain):
        return True
    # A direct provider field such as ``revenue_growth_rate`` is already one
    # structured fact.  Requiring two cited numeric inputs here distinguishes
    # a model-derived ratio/growth claim from that single direct observation.
    return numeric_input_count >= 2 and bool(_DERIVED_CLAIM_RE.search(plain))


def _citation_has_claim_cross_check(
    citation_id: str,
    groups: list[tuple[str, set[str]]],
    tier_by_citation: dict[str, str | None],
    check_tiers: set[str],
) -> bool:
    matching = [ids for _, ids in groups if citation_id in ids]
    if not matching:
        return False
    return all(
        any(other != citation_id and tier_by_citation.get(other) in check_tiers for other in ids)
        for ids in matching
    )


def _date_prefix(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    match = _ISO_DATE_RE.match(value)
    return match.group(0) if match else None


def _snake(value: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", value).lower()


__all__ = ["evaluate_citation_quality"]
