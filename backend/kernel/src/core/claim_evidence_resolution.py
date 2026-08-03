"""Claim-to-Evidence candidate retrieval, verification, and binding resolution.

The resolver intentionally separates high-recall retrieval from conservative
support verification.  Candidate scores only decide which bounded evidence
items are verified; they never prove support or trigger a repair by themselves.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from src.core.claim_audit import (
    ClaimCandidate,
    EvidenceSupport,
    canonical_evidence_metric,
    canonical_evidence_period,
    evidence_periods_compatible,
    match_composite_text_evidence,
    structured_units_compatible,
    structured_value_present,
    structured_values_equivalent,
    verify_evidence_support,
)

RESOLVER_REVISION = "claim-evidence-resolver-v1"
DEFAULT_CANDIDATE_LIMIT = 8

CandidateSignalName = Literal[
    "explicit-binding",
    "same-source",
    "entity-match",
    "metric-match",
    "period-match",
    "value-equivalent",
    "unit-compatible",
    "lexical-match",
    "document-adjacent",
]
ResolutionStatus = Literal[
    "verified",
    "supported-with-limits",
    "unresolved",
    "ambiguous",
    "invalid-binding",
    "contradicted",
    "calculation-invalid",
]
BindingAction = Literal["keep", "auto-bind", "auto-rebind", "none"]
RepairAction = Literal["none", "local-patch", "research-required"]
SemanticVerdict = Literal[
    "entailed",
    "partially-entailed",
    "unresolved",
    "contradicted",
    "unrelated",
]


@dataclass(frozen=True)
class CandidateSignal:
    name: CandidateSignalName
    score: float
    detail: str = ""


@dataclass(frozen=True)
class EvidenceCandidate:
    handle: str
    score: float
    signals: tuple[CandidateSignal, ...]
    hard_conflicts: tuple[str, ...]
    source: Mapping[str, Any]
    evidence: Mapping[str, Any]


@dataclass(frozen=True)
class SemanticVerificationResult:
    verdict: SemanticVerdict
    evidence_handles: tuple[str, ...]
    confidence: float
    covered_parts: tuple[str, ...] = ()
    missing_parts: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    verifier_revision: str = ""


class SemanticVerifierPort(Protocol):
    """Bounded semantic verifier; it cannot search or create Evidence."""

    def verify(
        self,
        claim: ClaimCandidate,
        candidates: tuple[EvidenceCandidate, ...],
    ) -> SemanticVerificationResult: ...


@dataclass(frozen=True)
class ClaimResolution:
    claim_id: str
    status: ResolutionStatus
    selected_handles: tuple[str, ...]
    candidate_handles: tuple[str, ...]
    binding_action: BindingAction
    repair_action: RepairAction
    support_by_handle: Mapping[str, str]
    reason_codes: tuple[str, ...]
    resolver_revision: str = RESOLVER_REVISION


def retrieve_evidence_candidates(
    claim: ClaimCandidate,
    records: Iterable[Any],
    *,
    semantics: Mapping[str, Any] | None = None,
    entity_aliases: Mapping[str, Iterable[str]] | None = None,
    limit: int = DEFAULT_CANDIDATE_LIMIT,
) -> tuple[EvidenceCandidate, ...]:
    """Return a bounded union of independently retrieved Evidence candidates."""

    explicit = set(claim.attached_evidence_handles)
    candidates: list[EvidenceCandidate] = []
    for index, record in enumerate(records):
        handle, source, evidence = _evidence_parts(record)
        if not handle or not isinstance(evidence, Mapping):
            continue
        signals, hard_conflicts = _candidate_signals(
            claim,
            handle,
            source,
            evidence,
            explicit=explicit,
            semantics=semantics,
            entity_aliases=entity_aliases,
        )
        # Keep a low-score registry fallback in the bounded ranking.  This is
        # important when an adapter omitted one canonical dimension: unknown
        # lowers rank but must not make the correct Evidence unreachable.
        score = sum(signal.score for signal in signals) - 30.0 * len(hard_conflicts)
        score -= index * 0.000001
        candidates.append(
            EvidenceCandidate(
                handle=handle,
                score=score,
                signals=tuple(signals),
                hard_conflicts=tuple(dict.fromkeys(hard_conflicts)),
                source=source,
                evidence=evidence,
            )
        )

    candidates.sort(
        key=lambda candidate: (
            candidate.handle not in explicit,
            -candidate.score,
            candidate.handle,
        )
    )
    if limit <= 0:
        return tuple(candidates)
    explicit_candidates = [candidate for candidate in candidates if candidate.handle in explicit]
    ranked = [candidate for candidate in candidates if candidate.handle not in explicit]
    return tuple((*explicit_candidates, *ranked[: max(0, limit - len(explicit_candidates))]))


def resolve_claim_evidence(
    claim: ClaimCandidate,
    records: Iterable[Any],
    *,
    semantics: Mapping[str, Any] | None = None,
    entity_aliases: Mapping[str, Iterable[str]] | None = None,
    semantic_verifier: SemanticVerifierPort | None = None,
    limit: int = DEFAULT_CANDIDATE_LIMIT,
) -> ClaimResolution:
    """Resolve one claim without mutating answer text or triggering repair."""

    candidates = retrieve_evidence_candidates(
        claim,
        records,
        semantics=semantics,
        entity_aliases=entity_aliases,
        limit=limit,
    )
    requested_explicit = tuple(dict.fromkeys(claim.attached_evidence_handles))
    explicit = tuple(
        handle
        for handle in requested_explicit
        if any(candidate.handle == handle for candidate in candidates)
    )
    missing_explicit = tuple(
        handle for handle in requested_explicit if handle not in explicit
    )
    support_by_handle: dict[str, str] = {}
    for candidate in candidates:
        support = _deterministic_support(claim, candidate, semantics)
        support_by_handle[candidate.handle] = support.status

    semantic_result: SemanticVerificationResult | None = None
    semantic_candidates = tuple(
        candidate
        for candidate in candidates
        if candidate.evidence.get("kind") == "text"
        and support_by_handle[candidate.handle] != "supported"
    )
    if semantic_verifier is not None and semantic_candidates:
        semantic_result = semantic_verifier.verify(claim, semantic_candidates)
        allowed = {candidate.handle for candidate in semantic_candidates}
        selected = tuple(
            handle for handle in semantic_result.evidence_handles if handle in allowed
        )
        mapped_status = {
            "entailed": "supported",
            "partially-entailed": "partially-supported",
            "unresolved": "not-found",
            "contradicted": "contradicted",
            "unrelated": "not-found",
        }[semantic_result.verdict]
        for handle in selected:
            support_by_handle[handle] = mapped_status

    supported = tuple(
        candidate.handle
        for candidate in candidates
        if support_by_handle[candidate.handle] == "supported"
    )
    partial = tuple(
        candidate.handle
        for candidate in candidates
        if support_by_handle[candidate.handle] == "partially-supported"
    )
    contradicted = tuple(
        candidate.handle
        for candidate in candidates
        if support_by_handle[candidate.handle] == "contradicted"
    )
    explicit_supported = tuple(handle for handle in explicit if handle in supported)
    explicit_contradicted = tuple(handle for handle in explicit if handle in contradicted)

    if explicit_supported:
        return _resolution(
            claim,
            "verified",
            explicit_supported,
            candidates,
            "keep",
            "none",
            support_by_handle,
            ("explicit-binding-supported",),
        )

    composite_handles = _composite_text_support(claim, candidates, semantics)
    if composite_handles:
        explicit_set = set(requested_explicit)
        composite_set = set(composite_handles)
        if explicit_set and composite_set.issubset(explicit_set):
            binding_action: BindingAction = "keep"
            reason = "explicit-composite-binding-supported"
        elif explicit_set:
            binding_action = "auto-rebind"
            reason = "unique-composite-replacement"
        else:
            binding_action = "auto-bind"
            reason = "composite-text-coverage"
        return _resolution(
            claim,
            "verified",
            composite_handles,
            candidates,
            binding_action,
            "none",
            support_by_handle,
            (reason,),
        )

    replacement = tuple(handle for handle in supported if handle not in explicit)
    if requested_explicit and len(replacement) == 1:
        return _resolution(
            claim,
            "verified",
            replacement,
            candidates,
            "auto-rebind",
            "none",
            support_by_handle,
            ("unique-verified-replacement",),
        )
    if not requested_explicit and len(supported) == 1:
        return _resolution(
            claim,
            "verified",
            supported,
            candidates,
            "auto-bind",
            "none",
            support_by_handle,
            ("unique-verified-candidate",),
        )
    if len(supported) > 1:
        return _resolution(
            claim,
            "ambiguous",
            (),
            candidates,
            "none",
            "none",
            support_by_handle,
            ("multiple-verified-candidates",),
        )
    if explicit_contradicted:
        return _resolution(
            claim,
            "contradicted",
            explicit_contradicted,
            candidates,
            "none",
            "local-patch",
            support_by_handle,
            ("explicit-binding-contradicted",),
        )
    if requested_explicit:
        return _resolution(
            claim,
            "invalid-binding",
            explicit,
            candidates,
            "none",
            "none",
            support_by_handle,
            (
                "explicit-binding-missing"
                if missing_explicit
                else "explicit-binding-unresolved",
            ),
        )
    if partial:
        return _resolution(
            claim,
            "supported-with-limits",
            partial,
            candidates,
            "none",
            "none",
            support_by_handle,
            ("partial-support-only",),
        )
    if contradicted:
        # An unbound contradictory-looking record is not enough to rewrite the
        # answer.  It remains unresolved until identity is uniquely proved.
        return _resolution(
            claim,
            "unresolved",
            (),
            candidates,
            "none",
            "none",
            support_by_handle,
            ("unbound-conflict-not-actionable",),
        )
    reason = (
        "semantic-verifier-unresolved"
        if semantic_result is not None
        else "no-verified-candidate"
    )
    return _resolution(
        claim,
        "unresolved",
        (),
        candidates,
        "none",
        "none",
        support_by_handle,
        (reason,),
    )


def _composite_text_support(
    claim: ClaimCandidate,
    candidates: tuple[EvidenceCandidate, ...],
    semantics: Mapping[str, Any] | None,
) -> tuple[str, ...]:
    text_candidates = [
        {
            "evidenceHandle": candidate.handle,
            "source": candidate.source,
            "evidence": candidate.evidence,
        }
        for candidate in candidates
        if candidate.evidence.get("kind") == "text"
        and not candidate.hard_conflicts
    ]
    handles = match_composite_text_evidence(
        claim,
        text_candidates,
        semantics=semantics,
    )
    return handles if 2 <= len(handles) <= 3 else ()


def _resolution(
    claim: ClaimCandidate,
    status: ResolutionStatus,
    selected: tuple[str, ...],
    candidates: tuple[EvidenceCandidate, ...],
    binding_action: BindingAction,
    repair_action: RepairAction,
    support_by_handle: Mapping[str, str],
    reason_codes: tuple[str, ...],
) -> ClaimResolution:
    return ClaimResolution(
        claim_id=claim.claim_id,
        status=status,
        selected_handles=selected,
        candidate_handles=tuple(candidate.handle for candidate in candidates),
        binding_action=binding_action,
        repair_action=repair_action,
        support_by_handle=dict(support_by_handle),
        reason_codes=reason_codes,
    )


def _deterministic_support(
    claim: ClaimCandidate,
    candidate: EvidenceCandidate,
    semantics: Mapping[str, Any] | None,
) -> EvidenceSupport:
    if "entity" in candidate.hard_conflicts:
        return EvidenceSupport("contradicted", 4)
    support = verify_evidence_support(
        claim,
        {"source": candidate.source, "evidence": candidate.evidence},
        semantics=semantics,
    )
    if support.status != "not-found" or candidate.evidence.get("kind") != "structured-data":
        return support

    # The legacy verifier checks value before identity and therefore reports a
    # mismatching value as not-found.  Once metric/period/entity/unit are all
    # explicitly compatible, a different value is a provable contradiction.
    evidence = candidate.evidence
    claim_metric = claim.normalized.get("metric", "")
    canonical_claim_metric = (
        canonical_evidence_metric({"metric": claim_metric}, semantics)
        if claim_metric
        else ""
    )
    evidence_metric = canonical_evidence_metric(evidence, semantics)
    # Compact calculation workups frequently repeat an already displayed
    # input as ``2026 Q1: 10,285,128,726 CNY``. The row has an exact value,
    # unit and period but intentionally omits the metric inherited from the
    # surrounding table. Treat that record as deterministically supported;
    # the resolver will auto-bind only when it is unique, while duplicate
    # values across metrics remain ambiguous and therefore unbound.
    if not canonical_claim_metric and evidence_metric:
        claim_period = claim.normalized.get("period", "")
        evidence_period = canonical_evidence_period(
            str(evidence.get("period") or evidence.get("asOf") or ""),
            semantics,
        )
        claim_unit = claim.normalized.get("unit", "")
        evidence_unit = str(evidence.get("unit") or "")
        if (
            structured_value_present(
                evidence.get("value"),
                evidence_unit,
                claim.exact,
                field=str(evidence.get("field") or ""),
                metric=str(evidence.get("metric") or ""),
                semantics=semantics,
            )
            and not (
                claim_period
                and evidence_period
                and not evidence_periods_compatible(claim_period, evidence_period)
            )
            and "entity" not in candidate.hard_conflicts
            and not (
                claim_unit
                and evidence_unit
                and not structured_units_compatible(
                    claim_unit,
                    evidence_unit,
                    semantics=semantics,
                )
            )
        ):
            return EvidenceSupport("supported", 3)
    if (
        not canonical_claim_metric
        or not evidence_metric
        or canonical_claim_metric != evidence_metric
    ):
        return support
    claim_period = claim.normalized.get("period", "")
    evidence_period = canonical_evidence_period(
        str(evidence.get("period") or evidence.get("asOf") or ""),
        semantics,
    )
    if claim_period and evidence_period and not evidence_periods_compatible(
        claim_period,
        evidence_period,
    ):
        return support
    if "entity" in candidate.hard_conflicts:
        return support
    claim_value = claim.normalized.get("value")
    evidence_value = evidence.get("value")
    if claim_value is None or evidence_value is None:
        return support
    claim_unit = claim.normalized.get("unit", "")
    evidence_unit = str(evidence.get("unit") or "")
    if claim_unit and evidence_unit and not structured_units_compatible(
        claim_unit,
        evidence_unit,
        semantics=semantics,
    ):
        return support
    if not structured_values_equivalent(
        claim_value,
        claim_unit,
        evidence_value,
        evidence_unit,
        semantics=semantics,
    ):
        return EvidenceSupport("contradicted", 4)
    return support


def _candidate_signals(
    claim: ClaimCandidate,
    handle: str,
    source: Mapping[str, Any],
    evidence: Mapping[str, Any],
    *,
    explicit: set[str],
    semantics: Mapping[str, Any] | None,
    entity_aliases: Mapping[str, Iterable[str]] | None,
) -> tuple[list[CandidateSignal], list[str]]:
    signals: list[CandidateSignal] = []
    conflicts: list[str] = []
    if handle in explicit:
        signals.append(CandidateSignal("explicit-binding", 100.0))

    kind = evidence.get("kind")
    if kind in {"structured-data", "calculation"}:
        value = evidence.get("value") if kind == "structured-data" else evidence.get("result")
        if value is not None and structured_value_present(
            value,
            str(evidence.get("unit") or ""),
            claim.exact,
            field=str(evidence.get("field") or ""),
            metric=str(evidence.get("metric") or ""),
            semantics=semantics,
        ):
            signals.append(CandidateSignal("value-equivalent", 40.0))
        _add_structured_identity_signals(
            claim,
            source,
            evidence,
            signals,
            conflicts,
            semantics,
            entity_aliases,
        )
    elif kind == "text":
        quote = _text_evidence(evidence)
        normalized_claim = _normalize_text(claim.exact)
        normalized_quote = _normalize_text(quote)
        if normalized_claim and normalized_claim in normalized_quote:
            signals.append(CandidateSignal("lexical-match", 60.0, "exact-normalized"))
        else:
            overlap = _token_overlap(normalized_claim, normalized_quote)
            if overlap > 0:
                signals.append(CandidateSignal("lexical-match", min(20.0, overlap * 20.0)))
        claim_numbers = set(_number_tokens(claim.exact))
        quote_numbers = set(_number_tokens(quote))
        if claim_numbers and claim_numbers.issubset(quote_numbers):
            signals.append(CandidateSignal("value-equivalent", 25.0, "all-number-tokens"))
        entity_relation = _entity_relation(
            claim.semantic_text,
            source,
            evidence,
            entity_aliases,
        )
        if entity_relation == "conflict":
            conflicts.append("entity")
        elif entity_relation == "match":
            signals.append(CandidateSignal("entity-match", 20.0))

    source_identity = str(
        source.get("documentId") or source.get("sourceId") or source.get("url") or ""
    )
    if source_identity and handle in explicit:
        signals.append(CandidateSignal("same-source", 10.0))
    return signals, conflicts


def _add_structured_identity_signals(
    claim: ClaimCandidate,
    source: Mapping[str, Any],
    evidence: Mapping[str, Any],
    signals: list[CandidateSignal],
    conflicts: list[str],
    semantics: Mapping[str, Any] | None,
    entity_aliases: Mapping[str, Iterable[str]] | None,
) -> None:
    raw_claim_metric = claim.normalized.get("metric", "")
    claim_metric = (
        canonical_evidence_metric({"metric": raw_claim_metric}, semantics)
        if raw_claim_metric
        else ""
    )
    metric_candidates = {
        canonical_evidence_metric({"metric": value}, semantics)
        for value in claim.normalized.get("metricCandidates", "").split("|")
        if value
    }
    evidence_metric = canonical_evidence_metric(evidence, semantics)
    if evidence_metric and (
        claim_metric == evidence_metric or evidence_metric in metric_candidates
    ):
        signals.append(CandidateSignal("metric-match", 25.0))
    elif claim_metric and evidence_metric and not metric_candidates:
        conflicts.append("metric")

    claim_period = claim.normalized.get("period", "")
    evidence_period = canonical_evidence_period(
        str(evidence.get("period") or evidence.get("asOf") or ""),
        semantics,
    )
    if claim_period and evidence_period:
        if evidence_periods_compatible(claim_period, evidence_period):
            signals.append(CandidateSignal("period-match", 20.0))
        else:
            conflicts.append("period")

    entity_relation = _entity_relation(
        claim.semantic_text,
        source,
        evidence,
        entity_aliases,
    )
    if entity_relation == "conflict":
        conflicts.append("entity")
    elif entity_relation == "match":
        signals.append(CandidateSignal("entity-match", 20.0))

    claim_unit = claim.normalized.get("unit", "")
    evidence_unit = str(evidence.get("unit") or "")
    if claim_unit and evidence_unit:
        if structured_units_compatible(claim_unit, evidence_unit, semantics=semantics):
            signals.append(CandidateSignal("unit-compatible", 8.0))


def _evidence_parts(
    record: Any,
) -> tuple[str, Mapping[str, Any], Mapping[str, Any]]:
    if isinstance(record, Mapping):
        handle = record.get("evidenceHandle") or record.get("handle")
        source = record.get("source")
        evidence = record.get("evidence")
    else:
        handle = getattr(record, "handle", None)
        source = getattr(record, "source", None)
        evidence = getattr(record, "evidence", None)
    return (
        str(handle or ""),
        source if isinstance(source, Mapping) else {},
        evidence if isinstance(evidence, Mapping) else {},
    )


def _text_evidence(evidence: Mapping[str, Any]) -> str:
    return " ".join(
        str(evidence.get(key) or "") for key in ("prefix", "quote", "suffix", "snippet")
    )


def _normalize_text(value: str) -> str:
    return re.sub(r"[^a-z0-9\u3400-\u9fff]+", " ", value.casefold()).strip()


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z][a-z0-9]+|[\u3400-\u9fff]{2,}", value))


def _token_overlap(left: str, right: str) -> float:
    left_tokens = _tokens(left)
    if not left_tokens:
        return 0.0
    return len(left_tokens & _tokens(right)) / len(left_tokens)


def _number_tokens(value: str) -> tuple[str, ...]:
    return tuple(
        token.replace(",", "")
        for token in re.findall(r"(?<![A-Za-z0-9_])[-+]?\d[\d,]*(?:\.\d+)?", value)
    )


def _entity_ids(value: str) -> set[str]:
    return set(re.findall(r"(?<!\d)\d{5,6}(?!\d)", value))


def _evidence_entity_text(
    source: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> str:
    return " ".join(
        str(value or "")
        for value in (
            evidence.get("entityId"),
            evidence.get("entityName"),
            evidence.get("recordKey"),
            source.get("title"),
            source.get("organization"),
            source.get("sourceId"),
        )
    )


def _normalize_entity_alias(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u3400-\u9fff]+", "", str(value).casefold())


def _alias_is_present(value: str, alias: str) -> bool:
    normalized = _normalize_entity_alias(alias)
    if len(normalized) < 2:
        return False
    if re.fullmatch(r"[a-z0-9]{2,12}", normalized):
        return bool(
            re.search(
                rf"(?<![A-Za-z0-9]){re.escape(normalized)}(?![A-Za-z0-9])",
                value,
                re.IGNORECASE,
            )
        )
    return normalized in _normalize_entity_alias(value)


def _canonical_entities(
    value: str,
    entity_aliases: Mapping[str, Iterable[str]] | None,
) -> set[str]:
    if not entity_aliases:
        return set()
    output: set[str] = set()
    for canonical, aliases in entity_aliases.items():
        canonical_key = _normalize_entity_alias(canonical)
        if not canonical_key:
            continue
        values = (canonical, *tuple(str(alias) for alias in aliases))
        if any(_alias_is_present(value, alias) for alias in values):
            output.add(canonical_key)
    return output


def _entity_relation(
    claim_text: str,
    source: Mapping[str, Any],
    evidence: Mapping[str, Any],
    entity_aliases: Mapping[str, Iterable[str]] | None,
) -> Literal["match", "conflict", "unknown"]:
    evidence_text = _evidence_entity_text(source, evidence)
    claim_entities = _canonical_entities(claim_text, entity_aliases)
    evidence_entities = _canonical_entities(evidence_text, entity_aliases)
    if len(claim_entities) == 1 and evidence_entities:
        return "match" if not claim_entities.isdisjoint(evidence_entities) else "conflict"

    claim_ids = _entity_ids(claim_text)
    evidence_ids = _entity_ids(evidence_text)
    if claim_ids and evidence_ids:
        return "match" if not claim_ids.isdisjoint(evidence_ids) else "conflict"
    return "unknown"


def evidence_entity_conflicts(
    claim_text: str,
    source: Mapping[str, Any],
    evidence: Mapping[str, Any],
    entity_aliases: Mapping[str, Iterable[str]] | None = None,
) -> bool:
    """Return only turn-locally provable cross-entity conflicts."""

    return _entity_relation(claim_text, source, evidence, entity_aliases) == "conflict"
