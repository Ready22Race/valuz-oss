"""Deterministic Markdown-aware claim extraction and evidence matching.

This module is deliberately runtime-neutral.  It extracts atomic, structurally
located claims from a complete assistant draft and performs only conservative
local matching against the current turn's trusted Evidence Registry.  A model
or edition may add stricter classification and verification, but it must not
turn a local ``none``/``ambiguous`` result into a trusted binding without
additional evidence.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from markdown_it import MarkdownIt

CLAIM_EXTRACTOR_REVISION = "claim-extractor-v1"
CLAIM_VERIFIER_REVISION = "claim-verifier-local-v1"
MAX_CLAIMS_PER_ANSWER = 1_000

_CITATION_LINK_RE = re.compile(
    r"\[([^\]\n]{0,240})\]\((citation|evidence)://([A-Za-z0-9_-]{1,160})\)"
)
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]\n]{0,240})\]\(([^)\n]+)\)")
_SENTENCE_BOUNDARY_RE = re.compile(r"(?:[!?。！？；;]+|\.(?!\d))(?=\s|$)")
_NUMBER_RE = re.compile(r"(?<![\w])[-+]?\d[\d,]*(?:\.\d+)?")
_FINANCIAL_NUMBER_RE = re.compile(
    r"(?<![\w])[-+]?\d[\d,]*(?:\.\d+)?\s*"
    r"(?:%|bp|bps|(?:USD|CNY|EUR|GBP|JPY|HKD)[kmb]?|百万元|亿元|万元|元|倍)",
    re.IGNORECASE,
)
_DATE_RE = re.compile(
    r"(?:\b(?:19|20)\d{2}(?:[-/]\d{1,2}(?:[-/]\d{1,2})?)?\b|"
    r"(?:19|20)\d{2}\s*年(?:\s*\d{1,2}\s*月(?:\s*\d{1,2}\s*日)?)?)"
)
_DERIVED_RE = re.compile(
    r"(?:同比|环比|复合增长|增长率|利润率|毛利率|净利率|占比|回报率|"
    r"\bCAGR\b|\byoy\b|\bqoq\b|\bgrowth(?: rate)?\b|\bmargin\b|\bratio\b)",
    re.IGNORECASE,
)
_REASONING_RE = re.compile(
    r"(?:我认为|我们认为|可能|或许|预计|推测|建议|值得关注|"
    r"\b(?:may|might|could|should|likely|suggests?|appears?|recommend)\b)",
    re.IGNORECASE,
)
_USER_PROVIDED_RE = re.compile(
    r"(?:你(?:说|提供|提到)|用户(?:说|提供|提到)|"
    r"\b(?:you said|you provided|according to you)\b)",
    re.IGNORECASE,
)
_NOT_FOUND_RE = re.compile(
    r"(?:未(?:找到|检索到|发现|查到)|没有(?:找到|检索到|发现|查到)|"
    r"无(?:相关|匹配|可用).{0,8}(?:资料|文档|结果|记录|数据)|"
    r"\b(?:no (?:matching|relevant) .{0,24} (?:was|were )?found|"
    r"could not find|unable to find|search returned no results?)\b)",
    re.IGNORECASE,
)
_SOURCE_HEADING_RE = re.compile(
    r"^(?:sources?|references?|citations?|来源|参考来源|引用来源|参考资料)\s*[:：]?$",
    re.IGNORECASE,
)
_DECLARATIVE_RE = re.compile(
    r"(?:是|为|有|达到|增长|下降|成立|发布|宣布|位于|属于|担任|"
    r"\b(?:is|are|was|were|has|have|had|founded|reported|announced|"
    r"serves?|became|increases?|increased|grows?|grew|rises?|rose|"
    r"decreases?|decreased|declines?|declined|falls?|fell|reached|located)\b)",
    re.IGNORECASE,
)
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*|[\u4e00-\u9fff]{2,}")
_FIELD_TOKEN_RE = re.compile(r"[a-z0-9]+")
_TRAILING_PUNCTUATION = ".!?。！？；;,:，："
_METRIC_STOP_WORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "by",
    "company",
    "for",
    "in",
    "is",
    "of",
    "the",
    "to",
    "was",
    "were",
    "year",
    "fy",
}


@dataclass(frozen=True)
class ClaimCandidate:
    """One deterministic claim candidate plus private editing coordinates."""

    claim_id: str
    exact: str
    segment_index: int
    kind: str
    citation_required: bool
    attached_citation_ids: tuple[str, ...]
    normalized: dict[str, str]
    location: dict[str, Any]
    semantic_text: str = field(repr=False, compare=False)
    insertion_offset: int = field(repr=False, compare=False)
    attached_evidence_handles: tuple[str, ...] = field(
        default=(),
        repr=False,
        compare=False,
    )

    def to_bundle_dict(
        self,
        *,
        citation_ids: Iterable[str] | None = None,
        bindings: list[dict[str, str]] | None = None,
        status: str,
        issue_codes: Iterable[str] = (),
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "claimId": self.claim_id,
            "exact": self.exact,
            "segmentIndex": self.segment_index,
            "citationRequired": self.citation_required,
            "citationIds": list(citation_ids or self.attached_citation_ids),
            "status": status,
            "issueCodes": list(dict.fromkeys(issue_codes)),
            "location": dict(self.location),
        }
        if bindings:
            result["bindings"] = bindings
        return result


@dataclass(frozen=True)
class EvidenceMatch:
    status: Literal["exact", "ambiguous", "none", "conflict"]
    handles: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvidenceSupport:
    status: Literal[
        "supported",
        "partially-supported",
        "contradicted",
        "not-found",
    ]
    directness: int


@dataclass(frozen=True)
class AutoBindResult:
    text: str
    claim_handles: dict[str, str]


@dataclass(frozen=True)
class _TableCell:
    content: str
    absolute_start: int
    absolute_end: int


class _ClaimAccumulator(list[ClaimCandidate]):
    """Bounded collector so hostile/accidental huge answers cannot fan out."""

    def __init__(self) -> None:
        super().__init__()
        self.truncated = False

    def append(self, item: ClaimCandidate) -> None:
        if len(self) >= MAX_CLAIMS_PER_ANSWER:
            self.truncated = True
            return
        super().append(item)


def extract_claims(
    answer: str,
    *,
    mode: str = "required-on-evidence",
    semantics: Mapping[str, Any] | None = None,
) -> list[ClaimCandidate]:
    """Parse *answer* as Markdown and return stable atomic claim candidates."""

    claims, _truncated = extract_claims_with_status(
        answer,
        mode=mode,
        semantics=semantics,
    )
    return claims


def extract_claims_with_status(
    answer: str,
    *,
    mode: str = "required-on-evidence",
    semantics: Mapping[str, Any] | None = None,
) -> tuple[list[ClaimCandidate], bool]:
    """Return claims plus an explicit bounded-extraction truncation flag."""

    if not answer.strip():
        return [], False
    parser = MarkdownIt("commonmark").enable("table")
    tokens = parser.parse(answer)
    line_offsets = _line_offsets(answer)
    claims = _ClaimAccumulator()
    block_index = -1
    list_stack: list[dict[str, int]] = []
    current_list_item: int | None = None
    table_block_index: int | None = None
    table_headers: list[str] = []
    table_row_cells: list[_TableCell] = []
    table_in_header = False
    table_data_row = -1
    inline_search_cursor: dict[tuple[int, int], int] = {}
    heading_context: dict[int, str] = {}
    pending_heading_level: int | None = None
    skip_remainder = False

    def next_block() -> int:
        nonlocal block_index
        block_index += 1
        return block_index

    for token in tokens:
        if skip_remainder or claims.truncated:
            break
        if token.type == "heading_open":
            tag = str(token.tag or "")
            pending_heading_level = int(tag[1:]) if tag.startswith("h") and tag[1:].isdigit() else 6
            continue
        if token.type == "heading_close":
            pending_heading_level = None
            continue
        if token.type in {"bullet_list_open", "ordered_list_open"}:
            list_stack.append({"block": next_block(), "item": -1})
            continue
        if token.type in {"bullet_list_close", "ordered_list_close"}:
            if list_stack:
                list_stack.pop()
            current_list_item = list_stack[-1]["item"] if list_stack else None
            continue
        if token.type == "list_item_open" and list_stack:
            list_stack[-1]["item"] += 1
            current_list_item = list_stack[-1]["item"]
            continue
        if token.type == "list_item_close":
            current_list_item = None
            continue
        if token.type == "table_open":
            table_block_index = next_block()
            table_headers = []
            table_data_row = -1
            continue
        if token.type == "table_close":
            table_block_index = None
            table_row_cells = []
            continue
        if token.type == "thead_open":
            table_in_header = True
            continue
        if token.type == "thead_close":
            table_in_header = False
            continue
        if token.type == "tr_open" and table_block_index is not None:
            table_row_cells = []
            if not table_in_header:
                table_data_row += 1
            continue
        if token.type == "tr_close" and table_block_index is not None:
            if table_in_header:
                table_headers = [_plain_text(cell.content) for cell in table_row_cells]
            else:
                _append_table_claims(
                    claims,
                    table_row_cells,
                    headers=table_headers,
                    block_index=table_block_index,
                    row_index=table_data_row,
                    mode=mode,
                    semantics=semantics,
                    normalization_context=" ".join(
                        heading_context[level] for level in sorted(heading_context)
                    ),
                )
            table_row_cells = []
            continue
        if token.type != "inline" or token.map is None:
            continue

        absolute_start, absolute_end = _locate_inline_source(
            answer,
            token.content,
            token.map,
            line_offsets,
            inline_search_cursor,
        )
        if pending_heading_level is not None:
            plain_heading = _plain_text(token.content).strip()
            if _SOURCE_HEADING_RE.fullmatch(plain_heading):
                skip_remainder = True
                continue
            for level in [level for level in heading_context if level >= pending_heading_level]:
                del heading_context[level]
            if plain_heading:
                heading_context[pending_heading_level] = plain_heading
            continue
        inherited_context = " ".join(heading_context[level] for level in sorted(heading_context))
        if table_block_index is not None:
            table_row_cells.append(
                _TableCell(
                    content=token.content,
                    absolute_start=absolute_start,
                    absolute_end=absolute_end,
                )
            )
            continue

        plain_block = _plain_text(token.content)
        if _SOURCE_HEADING_RE.fullmatch(plain_block.strip()):
            skip_remainder = True
            continue
        if list_stack:
            location_kind = "list-item"
            active_block = list_stack[-1]["block"]
            item_index = current_list_item if current_list_item is not None else 0
        else:
            location_kind = "text"
            active_block = next_block()
            item_index = None
        _append_inline_claims(
            claims,
            token.content,
            absolute_start=absolute_start,
            block_index=active_block,
            location_kind=location_kind,
            item_index=item_index,
            mode=mode,
            semantics=semantics,
            normalization_context=inherited_context,
        )
    return list(claims), claims.truncated


def match_available_evidence(
    claim: ClaimCandidate,
    records: Iterable[Any],
    *,
    semantics: Mapping[str, Any] | None = None,
) -> EvidenceMatch:
    """Return a unique exact Registry candidate or an explicit ambiguity."""

    exact: list[str] = []
    semantic_values: dict[tuple[str, str, str, str, str], set[str]] = {}
    semantic_handles: dict[tuple[str, str, str, str, str], list[str]] = {}
    for record in records:
        handle, _source, evidence = _evidence_parts(record)
        if not handle or not isinstance(evidence, Mapping):
            continue
        support = verify_evidence_support(claim, evidence, semantics=semantics)
        if support.status == "supported":
            exact.append(handle)
        if evidence.get("kind") != "structured-data":
            continue
        semantic_key = (
            _canonical_metric(evidence, semantics),
            _period_key(
                str(evidence.get("period") or evidence.get("asOf") or ""),
                semantics,
            ),
            _normalize_prose(str(evidence.get("entityId") or evidence.get("entityName") or "")),
            _canonical_dimension(str(evidence.get("scope") or ""), semantics, "scope"),
            _canonical_dimension(str(evidence.get("basis") or ""), semantics, "basis"),
        )
        if semantic_key[0] and _metric_matches_claim(
            semantic_key[0],
            claim,
            semantics,
        ):
            semantic_values.setdefault(semantic_key, set()).add(
                _semantic_value_key(evidence, semantics)
            )
            semantic_handles.setdefault(semantic_key, []).append(handle)

    conflicts = [
        handle
        for key, values in semantic_values.items()
        if len(values) > 1
        for handle in semantic_handles.get(key, [])
    ]
    if conflicts:
        return EvidenceMatch("conflict", tuple(dict.fromkeys(conflicts)))
    exact = list(dict.fromkeys(exact))
    if len(exact) == 1:
        return EvidenceMatch("exact", (exact[0],))
    if len(exact) > 1:
        return EvidenceMatch("ambiguous", tuple(exact))
    return EvidenceMatch("none")


def auto_bind_unique_claims(
    answer: str,
    records: Iterable[Any],
    *,
    mode: str = "required-on-evidence",
    semantics: Mapping[str, Any] | None = None,
) -> AutoBindResult:
    """Insert provisional links for unique exact matches at AST locations.

    Insertions are applied from the end of the Markdown document so offsets
    remain stable.  Existing evidence/citation bindings are never replaced.
    Ambiguous, conflicting, partial and missing matches are left untouched for
    the single repair/publication decision.
    """

    available = list(records)
    insertions: list[tuple[int, str]] = []
    claim_handles: dict[str, str] = {}
    for claim in extract_claims(answer, mode=mode, semantics=semantics):
        if (
            not claim.citation_required
            or claim.attached_citation_ids
            or claim.attached_evidence_handles
        ):
            continue
        match = match_available_evidence(claim, available, semantics=semantics)
        if match.status != "exact" or len(match.handles) != 1:
            continue
        handle = match.handles[0]
        insertions.append(
            (
                claim.insertion_offset,
                f" [source](evidence://{handle})",
            )
        )
        claim_handles[claim.claim_id] = handle
    text = answer
    for offset, markdown in sorted(insertions, reverse=True):
        text = f"{text[:offset]}{markdown}{text[offset:]}"
    return AutoBindResult(text=text, claim_handles=claim_handles)


def verify_evidence_support(
    claim: ClaimCandidate,
    evidence_container: Mapping[str, Any],
    *,
    semantics: Mapping[str, Any] | None = None,
) -> EvidenceSupport:
    """Conservatively verify one evidence snapshot against one claim."""

    evidence = evidence_container.get("evidence")
    if isinstance(evidence, Mapping):
        evidence_container = evidence
    kind = evidence_container.get("kind")
    if kind == "structured-data":
        value = evidence_container.get("value")
        if not _structured_value_matches_claim(value, evidence_container, claim, semantics):
            return EvidenceSupport("not-found", 0)
        metric = _canonical_metric(evidence_container, semantics)
        if not metric or not _metric_matches_claim(metric, claim, semantics):
            return EvidenceSupport("not-found", 0)
        entity_status = _entity_support_status(claim, evidence_container)
        if entity_status == "contradicted":
            return EvidenceSupport("contradicted", 2)
        evidence_period = _period_key(
            str(evidence_container.get("period") or evidence_container.get("asOf") or ""),
            semantics,
        )
        claim_period = claim.normalized.get("period", "")
        if claim_period and evidence_period and claim_period != evidence_period:
            return EvidenceSupport("contradicted", 2)
        evidence_unit = _canonical_unit(
            str(evidence_container.get("unit") or ""),
            semantics,
        )
        claim_unit = claim.normalized.get("unitBase") or claim.normalized.get("unit", "")
        if claim_unit and evidence_unit and not _units_compatible(claim_unit, evidence_unit):
            return EvidenceSupport("contradicted", 2)
        dimension_status = _dimension_support_status(
            claim,
            evidence_container,
            semantics,
        )
        if dimension_status == "contradicted":
            return EvidenceSupport("contradicted", 2)
        if entity_status == "partial" or dimension_status == "partial":
            return EvidenceSupport("partially-supported", 2)
        if len(_claim_amounts(claim.exact, semantics)) > 1:
            return EvidenceSupport("partially-supported", 2)
        return EvidenceSupport("supported", 4)
    if kind == "text":
        quote = _plain_text(str(evidence_container.get("quote") or ""))
        claim_text = _normalize_prose(claim.exact)
        quote_text = _normalize_prose(quote)
        if not quote_text:
            return EvidenceSupport("not-found", 0)
        if claim_text in quote_text or quote_text in claim_text:
            return EvidenceSupport("supported", 4)
        claim_tokens = _semantic_tokens(claim_text)
        quote_tokens = _semantic_tokens(quote_text)
        if claim_tokens and len(claim_tokens & quote_tokens) / len(claim_tokens) >= 0.6:
            return EvidenceSupport("partially-supported", 1)
        return EvidenceSupport("not-found", 0)
    if kind == "calculation":
        if not _value_present(evidence_container.get("result"), claim.exact):
            return EvidenceSupport("not-found", 0)
        if claim.kind != "calculation" and not _DERIVED_RE.search(claim.exact):
            return EvidenceSupport("partially-supported", 1)
        metric = _canonical_metric(evidence_container, semantics)
        if metric and not _metric_matches_claim(metric, claim, semantics):
            return EvidenceSupport("not-found", 0)
        entity_status = _entity_support_status(claim, evidence_container)
        if entity_status == "contradicted":
            return EvidenceSupport("contradicted", 2)
        evidence_period = _period_key(
            str(evidence_container.get("period") or ""),
            semantics,
        )
        claim_period = claim.normalized.get("period", "")
        if claim_period and evidence_period and claim_period != evidence_period:
            return EvidenceSupport("contradicted", 2)
        evidence_unit = _canonical_unit(
            str(evidence_container.get("unit") or ""),
            semantics,
        )
        claim_unit = claim.normalized.get("unitBase") or claim.normalized.get("unit", "")
        if claim_unit and evidence_unit and not _units_compatible(claim_unit, evidence_unit):
            return EvidenceSupport("contradicted", 2)
        dimension_status = _dimension_support_status(
            claim,
            evidence_container,
            semantics,
        )
        if dimension_status == "contradicted":
            return EvidenceSupport("contradicted", 2)
        if entity_status == "partial" or dimension_status == "partial":
            return EvidenceSupport("partially-supported", 2)
        return EvidenceSupport("supported", 3)
    return EvidenceSupport("not-found", 0)


def structured_value_present(
    value: Any,
    unit: str,
    text: str,
    *,
    semantics: Mapping[str, Any] | None = None,
) -> bool:
    """Return whether *text* contains *value* under the configured unit scale."""

    evidence = {"value": value, "unit": unit}
    claims = extract_claims(text, mode="strict-domain", semantics=semantics)
    return any(
        _structured_value_matches_claim(value, evidence, claim, semantics) for claim in claims
    ) or _value_present(value, text)


def structured_components_cover_claim(
    claim: ClaimCandidate,
    evidence_items: Iterable[Mapping[str, Any]],
    *,
    semantics: Mapping[str, Any] | None = None,
) -> bool:
    """Return true when structured component evidence covers every claim value."""

    amounts = _claim_amounts(claim.exact, semantics)
    if len(amounts) < 2:
        return False
    covered = [False] * len(amounts)
    for evidence in evidence_items:
        container = evidence.get("evidence")
        if isinstance(container, Mapping):
            evidence = container
        if evidence.get("kind") != "structured-data":
            continue
        metric = _canonical_metric(evidence, semantics)
        if not metric or not _metric_matches_claim(metric, claim, semantics):
            continue
        if _entity_support_status(claim, evidence) == "contradicted":
            continue
        evidence_period = _period_key(
            str(evidence.get("period") or evidence.get("asOf") or ""),
            semantics,
        )
        claim_period = claim.normalized.get("period", "")
        if claim_period and evidence_period and claim_period != evidence_period:
            continue
        for index, amount in enumerate(amounts):
            if _evidence_matches_amount(evidence, amount, semantics):
                covered[index] = True
    return all(covered)


def canonical_evidence_metric(
    evidence: Mapping[str, Any],
    semantics: Mapping[str, Any] | None = None,
) -> str:
    """Return the policy-canonical metric for a trusted evidence snapshot."""

    return _canonical_metric(evidence, semantics)


def canonical_evidence_dimension(
    value: str,
    semantics: Mapping[str, Any] | None,
    dimension: str,
) -> str:
    """Return the policy-canonical scope/basis value."""

    return _canonical_dimension(value, semantics, dimension)


def canonical_evidence_period(
    value: str,
    semantics: Mapping[str, Any] | None = None,
) -> str:
    """Return a comparable FY/Q/YTD/as-of period key."""

    return _period_key(value, semantics)


def _append_inline_claims(
    output: list[ClaimCandidate],
    content: str,
    *,
    absolute_start: int,
    block_index: int,
    location_kind: str,
    item_index: int | None,
    mode: str,
    semantics: Mapping[str, Any] | None,
    normalization_context: str = "",
) -> None:
    for sentence_start, sentence_end in _sentence_spans(content):
        sentence = content[sentence_start:sentence_end]
        clause_spans = _atomic_clause_spans(sentence, semantics)
        for clause_start, clause_end in clause_spans:
            raw_start = sentence_start + clause_start
            raw_end = sentence_start + clause_end
            _append_inline_claim(
                output,
                content,
                raw_start=raw_start,
                raw_end=raw_end,
                absolute_start=absolute_start,
                block_index=block_index,
                location_kind=location_kind,
                item_index=item_index,
                mode=mode,
                semantics=semantics,
                normalization_context=normalization_context,
            )


def _append_inline_claim(
    output: list[ClaimCandidate],
    content: str,
    *,
    raw_start: int,
    raw_end: int,
    absolute_start: int,
    block_index: int,
    location_kind: str,
    item_index: int | None,
    mode: str,
    semantics: Mapping[str, Any] | None,
    normalization_context: str,
) -> None:
    raw = content[raw_start:raw_end]
    exact = _plain_text(raw)
    if not _is_meaningful_claim(exact):
        return
    rendered_prefix = _plain_text(content[:raw_start])
    start = len(rendered_prefix) + (1 if raw_start > 0 and content[raw_start - 1].isspace() else 0)
    end = start + len(exact)
    location: dict[str, Any] = {
        "kind": location_kind,
        "blockIndex": block_index,
        "start": start,
        "end": end,
        "sourceStart": absolute_start + raw_start,
        "sourceEnd": absolute_start + raw_end,
    }
    if item_index is not None:
        location["itemIndex"] = item_index
    citation_ids, evidence_handles = _binding_refs(raw)
    insertion = absolute_start + _insertion_index(content, raw_start, raw_end)
    _append_claim(
        output,
        exact=exact,
        location=location,
        insertion_offset=insertion,
        citation_ids=citation_ids,
        evidence_handles=evidence_handles,
        mode=mode,
        semantics=semantics,
        normalization_context=normalization_context,
    )


def _append_table_claims(
    output: list[ClaimCandidate],
    cells: list[_TableCell],
    *,
    headers: list[str],
    block_index: int,
    row_index: int,
    mode: str,
    semantics: Mapping[str, Any] | None,
    normalization_context: str = "",
) -> None:
    if len(cells) < 2:
        return
    row_label = _plain_text(cells[0].content)
    if not row_label:
        return
    row_citations, row_handles = _binding_refs(cells[0].content)
    for column_index, cell in enumerate(cells[1:], start=1):
        value = _plain_text(cell.content)
        if not value or not (_NUMBER_RE.search(value) or _DECLARATIVE_RE.search(value)):
            continue
        header = headers[column_index] if column_index < len(headers) else str(column_index + 1)
        exact = f"{row_label} — {header}: {value}"
        citation_ids, handles = _binding_refs(cell.content)
        location = {
            "kind": "table-cell",
            "blockIndex": block_index,
            "rowIndex": row_index,
            "columnIndex": column_index,
            "sourceStart": cell.absolute_start,
            "sourceEnd": cell.absolute_end,
        }
        _append_claim(
            output,
            exact=exact,
            location=location,
            insertion_offset=cell.absolute_start
            + _insertion_index(cell.content, 0, len(cell.content)),
            citation_ids=tuple(dict.fromkeys((*row_citations, *citation_ids))),
            evidence_handles=tuple(dict.fromkeys((*row_handles, *handles))),
            mode=mode,
            semantics=semantics,
            normalization_context=normalization_context,
        )


def _append_claim(
    output: list[ClaimCandidate],
    *,
    exact: str,
    location: dict[str, Any],
    insertion_offset: int,
    citation_ids: tuple[str, ...],
    evidence_handles: tuple[str, ...],
    mode: str,
    semantics: Mapping[str, Any] | None,
    normalization_context: str = "",
) -> None:
    semantic_text = f"{normalization_context} {exact}".strip()
    kind = _classify_claim(exact)
    required = _citation_required(
        exact,
        kind=kind,
        has_binding=bool(citation_ids or evidence_handles),
        mode=mode,
    )
    # Source offsets are for exact DOM placement, but inserting a provisional
    # evidence link changes those raw Markdown offsets.  Claim identity must
    # survive the Guard's auto-bind-and-reaudit cycle, so hash only the stable
    # rendered/structural coordinates plus the normalized claim text.
    identity_location = {
        key: value for key, value in location.items() if key not in {"sourceStart", "sourceEnd"}
    }
    fingerprint = json.dumps(identity_location, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(f"{fingerprint}\0{exact}".encode()).hexdigest()[:20]
    output.append(
        ClaimCandidate(
            claim_id=f"clm_{digest}",
            exact=exact,
            segment_index=len(output),
            kind=kind,
            citation_required=required,
            attached_citation_ids=citation_ids,
            normalized=_normalize_claim(semantic_text, semantics),
            location=location,
            semantic_text=semantic_text,
            insertion_offset=insertion_offset,
            attached_evidence_handles=evidence_handles,
        )
    )


def _classify_claim(text: str) -> str:
    if _USER_PROVIDED_RE.search(text):
        return "user-provided"
    if _DERIVED_RE.search(text) and _NUMBER_RE.search(text):
        return "calculation"
    if _FINANCIAL_NUMBER_RE.search(text):
        return "financial-fact"
    if _DATE_RE.search(text):
        return "date-fact"
    if _NUMBER_RE.search(text):
        return "numeric-fact"
    if _REASONING_RE.search(text):
        return "reasoning"
    if re.search(r"[\"“”‘’][^\"“”‘’]+[\"“”‘’]", text):
        return "quotation"
    return "document-claim"


def _citation_required(
    text: str,
    *,
    kind: str,
    has_binding: bool,
    mode: str,
) -> bool:
    if has_binding:
        return True
    if kind == "user-provided":
        return False
    if kind == "reasoning":
        return False
    if kind == "document-claim" and _NOT_FOUND_RE.search(text):
        return False
    if kind in {
        "financial-fact",
        "numeric-fact",
        "date-fact",
        "quotation",
        "calculation",
    }:
        return True
    if mode == "strict-domain":
        return True
    return bool(_DECLARATIVE_RE.search(text))


def _normalize_claim(
    text: str,
    semantics: Mapping[str, Any] | None,
) -> dict[str, str]:
    result: dict[str, str] = {}
    period_match = _DATE_RE.search(text)
    period = _period_key(period_match.group(0), semantics) if period_match else ""
    if period_match:
        sentence_period = _period_key(text, semantics)
        period = sentence_period or period
    if period:
        result["period"] = period
    amount = _claim_amount(text, semantics)
    if amount is not None:
        raw_value, raw_unit, base_value, base_unit = amount
        result["value"] = _stable_scalar(raw_value)
        if raw_unit:
            result["unit"] = raw_unit
        if base_value is not None:
            result["valueBase"] = _stable_scalar(base_value)
        if base_unit:
            result["unitBase"] = base_unit
    metric_candidates = _claim_metric_candidates(text, semantics)
    if len(metric_candidates) == 1:
        result["metric"] = metric_candidates[0]
    elif metric_candidates:
        result["metricCandidates"] = "|".join(metric_candidates)
    else:
        metric_tokens = sorted(_semantic_tokens(text))
        if metric_tokens:
            result["metric"] = " ".join(metric_tokens)
    for dimension in ("scope", "basis"):
        candidates = _claim_dimension_candidates(text, semantics, dimension)
        if len(candidates) == 1:
            result[dimension] = candidates[0]
        elif candidates:
            result[f"{dimension}Candidates"] = "|".join(candidates)
    return result


def _binding_refs(text: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    citations: list[str] = []
    handles: list[str] = []
    for _label, scheme, identifier in _CITATION_LINK_RE.findall(text):
        target = citations if scheme == "citation" else handles
        if identifier not in target:
            target.append(identifier)
    return tuple(citations), tuple(handles)


def _plain_text(value: str) -> str:
    value = _CITATION_LINK_RE.sub("", value)
    value = _MARKDOWN_LINK_RE.sub(lambda match: match.group(1), value)
    value = re.sub(r"(?:\*\*|__|~~|`)", "", value)
    value = re.sub(r"\\([\\`*{}\[\]()#+.!_>-])", r"\1", value)
    value = re.sub(r"\s+", " ", value).strip()
    return re.sub(r"\s+([.!?。！？；;,:：，])", r"\1", value)


def _sentence_spans(value: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start = 0
    for match in _SENTENCE_BOUNDARY_RE.finditer(value):
        spans.append((start, match.end()))
        start = match.end()
        while start < len(value) and value[start].isspace():
            start += 1
    if start < len(value):
        spans.append((start, len(value)))
    return spans


def _atomic_clause_spans(
    value: str,
    semantics: Mapping[str, Any] | None,
) -> list[tuple[int, int]]:
    """Split only clearly independent metric/value clauses.

    Commas normally remain inside a claim.  Finance policy snapshots may
    provide a metric ontology; when two comma-separated clauses each contain
    a distinct recognized metric and a numeric value, keeping them together
    would make one citation appear to support multiple facts.  In that narrow
    case each clause becomes its own atomic claim.
    """

    if not _metric_ontology(semantics):
        return [(0, len(value))]
    boundaries = [match.span() for match in re.finditer(r"(?<!\d)[,，]|[,，](?!\d)", value)]
    if not boundaries:
        return [(0, len(value))]
    raw_spans: list[tuple[int, int]] = []
    start = 0
    for _boundary_start, boundary_end in boundaries:
        raw_spans.append((start, boundary_end))
        start = boundary_end
    raw_spans.append((start, len(value)))
    meaningful = []
    for start, end in raw_spans:
        clause = _plain_text(value[start:end])
        metrics = _claim_metric_candidates(clause, semantics)
        if not _NUMBER_RE.search(clause) or len(metrics) != 1:
            return [(0, len(value))]
        meaningful.append(metrics[0])
    if len(set(meaningful)) != len(meaningful):
        return [(0, len(value))]
    return raw_spans


def _is_meaningful_claim(text: str) -> bool:
    if len(text) < 4 or _SOURCE_HEADING_RE.fullmatch(text):
        return False
    if re.fullmatch(r"[-:：,，.。\s]+", text):
        return False
    return bool(_NUMBER_RE.search(text) or _DECLARATIVE_RE.search(text) or len(text.split()) >= 3)


def _insertion_index(content: str, start: int, end: int) -> int:
    index = end
    while index > start and content[index - 1].isspace():
        index -= 1
    if index > start and content[index - 1] in _TRAILING_PUNCTUATION:
        index -= 1
    return index


def _line_offsets(text: str) -> list[int]:
    offsets = [0]
    for line in text.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    if offsets[-1] < len(text):
        offsets.append(len(text))
    return offsets


def _locate_inline_source(
    answer: str,
    content: str,
    line_map: list[int],
    line_offsets: list[int],
    cursors: dict[tuple[int, int], int],
) -> tuple[int, int]:
    start_line, end_line = line_map
    start = line_offsets[min(start_line, len(line_offsets) - 1)]
    end = line_offsets[min(end_line, len(line_offsets) - 1)]
    key = (start_line, end_line)
    cursor = max(start, cursors.get(key, start))
    located = answer.find(content, cursor, end)
    if located < 0:
        located = answer.find(content, start, end)
    if located < 0:
        located = start
    absolute_end = min(located + len(content), len(answer))
    cursors[key] = absolute_end
    return located, absolute_end


def _evidence_parts(record: Any) -> tuple[str, Mapping[str, Any], Mapping[str, Any]]:
    if isinstance(record, Mapping):
        handle = record.get("evidenceHandle") or record.get("handle")
        source = record.get("source")
        evidence = record.get("evidence")
    else:
        handle = getattr(record, "handle", None)
        source = getattr(record, "source", None)
        evidence = getattr(record, "evidence", None)
    return (
        str(handle) if isinstance(handle, str) else "",
        source if isinstance(source, Mapping) else {},
        evidence if isinstance(evidence, Mapping) else {},
    )


def _normalize_field(value: str) -> str:
    return " ".join(_FIELD_TOKEN_RE.findall(value.lower().replace("-", "_")))


def _field_matches_claim(field: str, claim: str) -> bool:
    field_tokens = {token for token in _FIELD_TOKEN_RE.findall(field) if token}
    claim_tokens = _semantic_tokens(claim)
    meaningful = field_tokens - _METRIC_STOP_WORDS
    if not meaningful:
        return False
    return meaningful.issubset(claim_tokens) or (
        len(meaningful) > 1 and len(meaningful & claim_tokens) >= len(meaningful) - 1
    )


def _metric_ontology(
    semantics: Mapping[str, Any] | None,
) -> Mapping[str, Any]:
    if not isinstance(semantics, Mapping):
        return {}
    ontology = semantics.get("metric_ontology")
    if isinstance(ontology, Mapping):
        metrics = ontology.get("metrics")
        return metrics if isinstance(metrics, Mapping) else {}
    metrics = semantics.get("metrics")
    return metrics if isinstance(metrics, Mapping) else {}


def _metric_terms(
    metric_id: str,
    definition: Any,
) -> tuple[str, ...]:
    values: list[str] = [metric_id]
    if isinstance(definition, Mapping):
        for key in ("aliases", "fields"):
            raw = definition.get(key)
            if isinstance(raw, list):
                values.extend(str(item) for item in raw if isinstance(item, str) and item)
    return tuple(dict.fromkeys(values))


def _term_in_text(term: str, text: str) -> bool:
    normalized_term = _normalize_prose(term.replace("_", " "))
    normalized_text = _normalize_prose(text.replace("_", " "))
    if not normalized_term:
        return False
    if re.search(r"[\u4e00-\u9fff]", normalized_term):
        return normalized_term.replace(" ", "") in normalized_text.replace(" ", "")
    return bool(
        re.search(
            rf"(?<![a-z0-9]){re.escape(normalized_term)}(?![a-z0-9])",
            normalized_text,
        )
    )


def _claim_metric_candidates(
    text: str,
    semantics: Mapping[str, Any] | None,
) -> tuple[str, ...]:
    matches: list[tuple[int, str]] = []
    for metric_id, definition in _metric_ontology(semantics).items():
        if not isinstance(metric_id, str) or not metric_id:
            continue
        lengths = [
            len(term) for term in _metric_terms(metric_id, definition) if _term_in_text(term, text)
        ]
        if lengths:
            matches.append((max(lengths), metric_id))
    if not matches:
        return ()
    longest = max(length for length, _metric in matches)
    return tuple(sorted({metric for length, metric in matches if length == longest}))


def _canonical_metric(
    evidence: Mapping[str, Any],
    semantics: Mapping[str, Any] | None,
) -> str:
    ontology = _metric_ontology(semantics)
    explicit = evidence.get("metric")
    if isinstance(explicit, str) and explicit:
        if explicit in ontology:
            return explicit
        normalized_explicit = _normalize_field(explicit)
        for metric_id, definition in ontology.items():
            if not isinstance(metric_id, str) or not isinstance(definition, Mapping):
                continue
            fields = definition.get("fields")
            if isinstance(fields, list) and normalized_explicit in {
                _normalize_field(item) for item in fields if isinstance(item, str) and item
            }:
                return metric_id
        return normalized_explicit
    field = str(evidence.get("field") or "")
    normalized_field = _normalize_field(field)
    for metric_id, definition in ontology.items():
        if not isinstance(metric_id, str):
            continue
        normalized_terms = {
            normalized
            for term in _metric_terms(metric_id, definition)
            if (normalized := _normalize_field(term))
        }
        if normalized_field in normalized_terms:
            return metric_id
    return normalized_field


def _metric_matches_claim(
    metric: str,
    claim: ClaimCandidate,
    semantics: Mapping[str, Any] | None,
) -> bool:
    ambiguous = claim.normalized.get("metricCandidates")
    if ambiguous:
        return False
    normalized_metric = claim.normalized.get("metric")
    if _metric_ontology(semantics) and normalized_metric:
        return normalized_metric == metric
    return _field_matches_claim(metric, claim.exact)


def _unit_definitions(
    semantics: Mapping[str, Any] | None,
) -> list[tuple[str, tuple[str, ...], Decimal]]:
    if not isinstance(semantics, Mapping):
        return []
    ontology = semantics.get("unit_ontology")
    ontology = ontology if isinstance(ontology, Mapping) else semantics
    raw_units = ontology.get("units") if isinstance(ontology, Mapping) else None
    definitions: list[tuple[str, tuple[str, ...], Decimal]] = []
    if isinstance(raw_units, Mapping):
        iterable = [
            (
                str(definition.get("canonical") or unit_id)
                if isinstance(definition, Mapping)
                else str(unit_id),
                definition,
            )
            for unit_id, definition in raw_units.items()
        ]
    elif isinstance(raw_units, list):
        iterable = [
            (str(item.get("id") or item.get("canonical") or ""), item)
            for item in raw_units
            if isinstance(item, Mapping)
        ]
    else:
        iterable = []
    for unit_id, definition in iterable:
        if not unit_id or not isinstance(definition, Mapping):
            continue
        aliases = definition.get("aliases")
        terms = [unit_id]
        if isinstance(aliases, list):
            terms.extend(str(item) for item in aliases if isinstance(item, str) and item)
        try:
            scale = Decimal(str(definition.get("scale", 1)))
        except (InvalidOperation, ValueError):
            continue
        definitions.append((unit_id, tuple(dict.fromkeys(terms)), scale))
    return definitions


def _resolve_unit(
    raw_unit: str,
    semantics: Mapping[str, Any] | None,
) -> tuple[str, Decimal] | None:
    normalized = _normalize_prose(raw_unit).replace(" ", "")
    if not normalized:
        return None
    for unit_id, aliases, scale in _unit_definitions(semantics):
        if any(_normalize_prose(alias).replace(" ", "") == normalized for alias in aliases):
            return unit_id, scale
    return None


def _canonical_unit(
    raw_unit: str,
    semantics: Mapping[str, Any] | None,
) -> str:
    resolved = _resolve_unit(raw_unit, semantics)
    return resolved[0] if resolved else raw_unit.strip()


def _claim_amount(
    text: str,
    semantics: Mapping[str, Any] | None,
) -> tuple[str, str, Decimal | None, str] | None:
    amounts = _claim_amounts(text, semantics)
    return amounts[0] if amounts else None


def _claim_amounts(
    text: str,
    semantics: Mapping[str, Any] | None,
) -> list[tuple[str, str, Decimal | None, str]]:
    unit_terms: list[tuple[str, str, Decimal]] = []
    for unit_id, aliases, scale in _unit_definitions(semantics):
        unit_terms.extend((alias, unit_id, scale) for alias in aliases)
    unit_terms.sort(key=lambda item: len(item[0]), reverse=True)
    amounts: list[tuple[int, tuple[str, str, Decimal | None, str]]] = []
    occupied: list[tuple[int, int]] = []
    for alias, unit_id, scale in unit_terms:
        pattern = re.compile(
            rf"(?P<value>[-+]?\d[\d,]*(?:\.\d+)?)\s*{re.escape(alias)}",
            re.IGNORECASE,
        )
        for match in pattern.finditer(text):
            if any(start <= match.start() < end for start, end in occupied):
                continue
            raw_value = match.group("value")
            decimal = _as_decimal(raw_value)
            occupied.append(match.span())
            amounts.append(
                (
                    match.start(),
                    (
                        raw_value,
                        match.group(0)[len(raw_value) :].strip(),
                        (decimal * scale if decimal is not None else None),
                        unit_id,
                    ),
                )
            )
    if amounts:
        return [item for _offset, item in sorted(amounts)]

    built_in_pattern = re.compile(
        r"(?P<value>[-+]?\d[\d,]*(?:\.\d+)?)\s*"
        r"(?P<unit>%|bp|bps|(?:USD|CNY|EUR|GBP|JPY|HKD)[kmb]?|百万元|亿元|万元|元|倍)",
        re.IGNORECASE,
    )
    built_in = list(built_in_pattern.finditer(text))
    if built_in:
        return [(match.group("value"), match.group("unit"), None, "") for match in built_in]

    period = _period_key(text, semantics)
    fallback: list[tuple[str, str, Decimal | None, str]] = []
    for match in _NUMBER_RE.finditer(text):
        raw_value = match.group(0)
        if period and raw_value in period:
            continue
        if len(raw_value.replace(",", "")) in {5, 6} and _looks_like_identifier(
            text,
            match.start(),
            match.end(),
        ):
            continue
        fallback.append((raw_value, "", None, ""))
    return fallback


def _as_decimal(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _looks_like_identifier(text: str, start: int, end: int) -> bool:
    prefix = text[max(0, start - 2) : start]
    suffix = text[end : end + 2]
    return "(" in prefix or "（" in prefix or ")" in suffix or "）" in suffix


def _structured_value_matches_claim(
    value: Any,
    evidence: Mapping[str, Any],
    claim: ClaimCandidate,
    semantics: Mapping[str, Any] | None,
) -> bool:
    for amount in _claim_amounts(claim.exact, semantics):
        if _evidence_matches_amount(evidence, amount, semantics):
            return True
    return _value_present(value, claim.exact)


def _evidence_matches_amount(
    evidence: Mapping[str, Any],
    amount: tuple[str, str, Decimal | None, str],
    semantics: Mapping[str, Any] | None,
) -> bool:
    raw_value, raw_unit, claim_base, claim_base_unit = amount
    evidence_decimal = _as_decimal(evidence.get("value"))
    if evidence_decimal is None:
        return False
    resolved = _resolve_unit(str(evidence.get("unit") or ""), semantics)
    if claim_base is not None and resolved:
        claim_unit = _resolve_unit(raw_unit, semantics)
        display_tolerance = (
            _display_rounding_tolerance(raw_value, claim_unit[1])
            if claim_unit is not None
            else Decimal(0)
        )
        return claim_base_unit == resolved[0] and _decimal_close(
            claim_base,
            evidence_decimal * resolved[1],
            minimum_tolerance=display_tolerance,
        )
    raw_decimal = _as_decimal(raw_value)
    return raw_decimal is not None and _decimal_close(raw_decimal, evidence_decimal)


def _display_rounding_tolerance(raw_value: str, unit_scale: Decimal) -> Decimal:
    normalized = raw_value.replace(",", "").lstrip("+-")
    decimal_places = len(normalized.rsplit(".", 1)[1]) if "." in normalized else 0
    display_step = Decimal(1).scaleb(-decimal_places) * unit_scale
    return display_step / Decimal(2)


def _decimal_close(
    left: Decimal,
    right: Decimal,
    *,
    minimum_tolerance: Decimal = Decimal(0),
) -> bool:
    tolerance = max(
        max(abs(left), abs(right), Decimal(1)) * Decimal("0.0000001"),
        minimum_tolerance,
    )
    return abs(left - right) <= tolerance


def _semantic_value_key(
    evidence: Mapping[str, Any],
    semantics: Mapping[str, Any] | None,
) -> str:
    value = evidence.get("value")
    decimal = _as_decimal(value)
    resolved = _resolve_unit(str(evidence.get("unit") or ""), semantics)
    if decimal is not None and resolved:
        return f"{_stable_scalar(decimal * resolved[1])} {resolved[0]}"
    return _stable_scalar(value)


def _entity_support_status(
    claim: ClaimCandidate,
    evidence: Mapping[str, Any],
) -> Literal["supported", "partial", "contradicted"]:
    claim_ids = _claim_entity_ids(claim.semantic_text)
    evidence_text = " ".join(
        str(evidence.get(key) or "") for key in ("entityId", "entityName", "recordKey")
    )
    evidence_ids = set(re.findall(r"(?<!\d)\d{5,6}(?!\d)", evidence_text))
    if claim_ids and evidence_ids and claim_ids.isdisjoint(evidence_ids):
        return "contradicted"
    if claim_ids and not evidence_ids:
        return "partial"
    entity_name = str(evidence.get("entityName") or "").strip()
    if entity_name and entity_name not in claim.semantic_text and not claim_ids:
        return "partial"
    return "supported"


def _claim_entity_ids(text: str) -> set[str]:
    patterns = (
        r"[（(]\s*(\d{5,6})\s*[)）]",
        r"(?:股票代码|证券代码|代码|ticker|symbol)\s*[:：]?\s*(\d{5,6})",
        r"^\s*(\d{5,6})(?=\s|[（(])",
    )
    return {
        match.group(1)
        for pattern in patterns
        for match in re.finditer(pattern, text, re.IGNORECASE)
    }


def _dimension_ontology(
    semantics: Mapping[str, Any] | None,
    dimension: str,
) -> Mapping[str, Any]:
    if not isinstance(semantics, Mapping):
        return {}
    dimensions = semantics.get("dimensions")
    if not isinstance(dimensions, Mapping):
        return {}
    values = dimensions.get(dimension)
    return values if isinstance(values, Mapping) else {}


def _claim_dimension_candidates(
    text: str,
    semantics: Mapping[str, Any] | None,
    dimension: str,
) -> tuple[str, ...]:
    found: set[str] = set()
    for value_id, aliases in _dimension_ontology(semantics, dimension).items():
        if not isinstance(value_id, str):
            continue
        terms = [value_id]
        if isinstance(aliases, list):
            terms.extend(str(item) for item in aliases if isinstance(item, str))
        if any(_term_in_text(term, text) for term in terms):
            found.add(value_id)
    return tuple(sorted(found))


def _canonical_dimension(
    raw_value: str,
    semantics: Mapping[str, Any] | None,
    dimension: str,
) -> str:
    candidates = _claim_dimension_candidates(raw_value, semantics, dimension)
    return candidates[0] if len(candidates) == 1 else _normalize_prose(raw_value)


def _dimension_support_status(
    claim: ClaimCandidate,
    evidence: Mapping[str, Any],
    semantics: Mapping[str, Any] | None,
) -> Literal["supported", "partial", "contradicted"]:
    for dimension in ("scope", "basis"):
        claim_value = claim.normalized.get(dimension)
        if not claim_value:
            continue
        evidence_raw = str(evidence.get(dimension) or "")
        if not evidence_raw:
            return "partial"
        if _canonical_dimension(evidence_raw, semantics, dimension) != claim_value:
            return "contradicted"
    return "supported"


def _semantic_tokens(value: str) -> set[str]:
    tokens = {
        token.lower()
        for token in _WORD_RE.findall(value.replace("_", " "))
        if token.lower() not in _METRIC_STOP_WORDS
    }
    return {token for token in tokens if not token.isdigit()}


def _period_key(
    value: str,
    semantics: Mapping[str, Any] | None = None,
) -> str:
    del semantics  # Reserved for edition-specific aliases carried in snapshots.
    compact = re.sub(r"\s+", "", value).upper()
    year_match = re.search(r"(?:19|20)\d{2}", compact)
    if not year_match:
        return ""
    year = year_match.group(0)
    date_range = re.search(
        rf"{year}-01-01[/~至到]{year}-(\d{{2}})-(\d{{2}})",
        compact,
    )
    if date_range:
        month_day = (date_range.group(1), date_range.group(2))
        return {
            ("03", "31"): f"{year} Q1 YTD",
            ("06", "30"): f"{year} H1",
            ("09", "30"): f"{year} Q3 YTD",
            ("12", "31"): f"{year} FY",
        }.get(month_day, f"{year}-{month_day[0]}-{month_day[1]}")
    if re.search(r"(?:Q?1YTD|第一季度|一季度)", compact):
        return f"{year} Q1 YTD"
    if re.search(
        r"(?:Q?3YTD|Q3(?:\(9MONTHS?\)|9MONTHS?)|前三季度|三季度累计)",
        compact,
    ):
        return f"{year} Q3 YTD"
    if re.search(r"(?:H1|上半年|半年度)", compact):
        return f"{year} H1"
    quarter = re.search(r"Q([1-4])", compact)
    if quarter:
        return f"{year} Q{quarter.group(1)}"
    chinese_quarter = re.search(r"第?([一二三四])季度", compact)
    if chinese_quarter:
        number = {"一": "1", "二": "2", "三": "3", "四": "4"}[chinese_quarter.group(1)]
        return f"{year} Q{number}"
    full_date = re.search(rf"{year}[-/](\d{{1,2}})[-/](\d{{1,2}})", compact)
    if full_date:
        return f"{year}-{int(full_date.group(1)):02d}-{int(full_date.group(2)):02d}"
    return f"{year} FY"


def _stable_scalar(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return str(value)
    try:
        decimal = Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError):
        return str(value)
    if not decimal.is_finite():
        return str(value)
    return format(decimal.normalize(), "f")


def _value_present(value: Any, text: str) -> bool:
    target = _stable_scalar(value)
    for match in _NUMBER_RE.findall(text):
        if _stable_scalar(match) == target:
            return True
    return bool(target and target.lower() in text.lower())


def _normalize_prose(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff%]+", " ", value.lower()).strip()


def _units_compatible(left: str, right: str) -> bool:
    aliases = {"percent": "%", "percentage": "%", "bps": "bp"}
    return aliases.get(left.lower(), left.lower()) == aliases.get(right.lower(), right.lower())


__all__ = [
    "CLAIM_EXTRACTOR_REVISION",
    "CLAIM_VERIFIER_REVISION",
    "AutoBindResult",
    "ClaimCandidate",
    "EvidenceMatch",
    "EvidenceSupport",
    "canonical_evidence_dimension",
    "canonical_evidence_metric",
    "canonical_evidence_period",
    "extract_claims",
    "extract_claims_with_status",
    "auto_bind_unique_claims",
    "match_available_evidence",
    "structured_components_cover_claim",
    "structured_value_present",
    "verify_evidence_support",
]
