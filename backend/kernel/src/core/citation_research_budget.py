"""Runtime-neutral limits and decisions for citation research.

Citation research must stop the same way regardless of the selected runtime.
This module owns the bounded document workflow; adapters only translate a
denial into their native hook or tool-message shape.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

DISCOVERY_TOOL_CALL_LIMIT = 6
DOCUMENT_FETCH_CALL_LIMIT = 3
DOCUMENT_FETCH_CHUNK_LIMIT = 60
RESEARCH_MODEL_CALL_LIMIT = 10
RESEARCH_FINALIZATION_ATTEMPT_LIMIT = 2
NON_DOCUMENT_SEARCH_TOOLS = {
    "agent_search",
    "company_search",
    "skill_search",
}
TRANSCRIPT_DISCOVERY_TOOLS = {"conferences_search", "minutes_search"}
TRANSCRIPT_DISCOVERY_RESULT_FLOOR = 20
TRANSCRIPT_INDEXED_CHUNK_LIMIT = 10
TRANSCRIPT_INDEXED_DOCUMENT_SEARCH_LIMIT = 6

_STABLE_KNOWLEDGE_INTENT_RE = re.compile(
    r"(?:"
    r"是什么|什么意思|什么含义|定义是什么|怎么理解|如何理解|"
    r"通俗(?:地|语言)?解释|解释一下|计算公式|公式是什么|怎么计算|如何计算|"
    r"\bwhat\s+is\b|\bwhat\s+does\b.{0,60}\bmean\b|\bdefinition\b|"
    r"\bexplain\b|\bformula\b|\bhow\s+(?:is|do|does|to)\b.{0,60}\bcalculate"
    r")",
    re.IGNORECASE,
)
_EXTERNAL_EVIDENCE_INTENT_RE = re.compile(
    r"(?:"
    r"引用|来源|出处|查找|查询|搜索|检索|根据|来自|"
    r"最新|近期|目前|当前|今天|截至|今年|本季度|过去|"
    r"报告|文档|文件|网页|新闻|财报|公告|电话会|原文|"
    r"数据|数值|多少|价格|股价|业绩|排名|比较|对比|列出|总结|"
    r"\bcite\b|\bcitation\b|\bsource\b|\bsearch\b|\blook\s+up\b|"
    r"\blatest\b|\bcurrent\b|\btoday\b|\bas\s+of\b|\brecent\b|"
    r"\breport\b|\bdocument\b|\bfiling\b|\bnews\b|\bdata\b|"
    r"\bhow\s+much\b|\bcompare\b|\blist\b|\bsummar"
    r")",
    re.IGNORECASE,
)
_URL_OR_FILE_RE = re.compile(r"https?://|\bwww\.|\b\S+\.(?:pdf|docx?|xlsx?|pptx?|md)\b", re.I)


def is_stable_general_knowledge_query(text: str) -> bool:
    """Identify a narrow no-research definition/explanation turn.

    This routing decision is intentionally conservative.  It prevents a model
    from spending search and repair budget on timeless educational questions,
    while any request for current facts, source material, documents, data, or
    a dated period remains on the normal evidence path.
    """

    normalized = " ".join(str(text or "").split())
    if not normalized or len(normalized) > 500:
        return False
    if _URL_OR_FILE_RE.search(normalized):
        return False
    if re.search(r"\b(?:19|20)\d{2}\b|(?:19|20)\d{2}\s*年", normalized):
        return False
    if _EXTERNAL_EVIDENCE_INTENT_RE.search(normalized):
        return False
    return _STABLE_KNOWLEDGE_INTENT_RE.search(normalized) is not None


def is_document_discovery_tool(tool_name: str | None) -> bool:
    """Return whether a tool consumes the shared document discovery budget."""

    name = str(tool_name or "").rsplit("__", 1)[-1]
    if name in NON_DOCUMENT_SEARCH_TOOLS:
        return False
    return name.endswith("_search") or name in {"search", "docs_list", "docs_by_tags"}


@dataclass(frozen=True)
class ResearchBudgetDecision:
    """One deterministic research-budget decision."""

    allowed: bool
    code: str | None = None
    reason: str | None = None
    chunk_limit: int | None = None


@dataclass
class CitationResearchBudget:
    """Per-turn citation discovery and original-document budget."""

    discovery_calls: int = 0
    document_fetch_calls: int = 0
    document_fetch_windows: dict[str, list[tuple[int, int]]] = field(
        default_factory=dict
    )
    complete_document_ids: set[str] = field(default_factory=set)
    indexed_document_search_ids: set[str] = field(default_factory=set)

    def reset(self) -> None:
        self.discovery_calls = 0
        self.document_fetch_calls = 0
        self.document_fetch_windows.clear()
        self.complete_document_ids.clear()
        self.indexed_document_search_ids.clear()

    @property
    def has_research_activity(self) -> bool:
        return bool(
            self.discovery_calls > 0
            or self.document_fetch_calls > 0
            or self.indexed_document_search_ids
        )

    def allow_discovery(self) -> ResearchBudgetDecision:
        if self.discovery_calls >= DISCOVERY_TOOL_CALL_LIMIT:
            return ResearchBudgetDecision(
                allowed=False,
                code="discovery-budget-exhausted",
                reason=(
                    "The document discovery budget for this turn is exhausted. "
                    "Do not run more search queries. Use the candidates already "
                    "returned, fetch a selected original document when available, "
                    "or finish from the evidence already collected."
                ),
            )
        self.discovery_calls += 1
        return ResearchBudgetDecision(allowed=True)

    def allow_indexed_document_search(
        self,
        document_ids: Sequence[str],
    ) -> ResearchBudgetDecision:
        """Reserve one precise indexed search for one selected document.

        A multi-document transcript answer needs one bounded search per source,
        not several overlapping raw/fetch/search passes.  Requiring a single
        document id also keeps every returned chunk attributable to one source.
        """

        normalized = tuple(
            dict.fromkeys(str(document_id).strip() for document_id in document_ids)
        )
        normalized = tuple(document_id for document_id in normalized if document_id)
        if len(normalized) != 1:
            return ResearchBudgetDecision(
                allowed=False,
                code="indexed-search-document-scope",
                reason=(
                    "Run the indexed document search for exactly one selected "
                    "doc_id at a time. This keeps each evidence chunk bound to "
                    "one original document."
                ),
            )
        document_id = normalized[0]
        if document_id in self.indexed_document_search_ids:
            return ResearchBudgetDecision(
                allowed=False,
                code="duplicate-indexed-document-search",
                reason=(
                    "This selected document already had its one targeted indexed "
                    "search in this turn. All available evidence for this source is "
                    "already in context. Do not call any more tools for this source. "
                    "Draft the final answer now and attach the returned exact "
                    "evidenceHandle values to the claims they support."
                ),
            )
        if (
            len(self.indexed_document_search_ids)
            >= TRANSCRIPT_INDEXED_DOCUMENT_SEARCH_LIMIT
        ):
            return ResearchBudgetDecision(
                allowed=False,
                code="indexed-document-search-budget-exhausted",
                reason=(
                    "The per-document indexed-search budget for this turn is "
                    "exhausted. Use the original chunks and evidence handles "
                    "already returned and finish the answer."
                ),
            )
        # Reserve before execution so parallel calls cannot duplicate the same
        # document or oversubscribe the per-document evidence budget. Indexed
        # retrieval is intentionally separate from candidate discovery: a
        # model that discovers four periods separately must not lose the later
        # periods merely because the first two original-document searches used
        # the remaining generic discovery slots.
        self.indexed_document_search_ids.add(document_id)
        return ResearchBudgetDecision(allowed=True)

    def allow_document_read(
        self,
        *,
        tool_name: str,
        document_id: str,
        chunk_offset: int = 0,
        requested_chunk_limit: int | None = None,
        allow_sequential_window: bool = False,
    ) -> ResearchBudgetDecision:
        if document_id and document_id in self.complete_document_ids:
            return ResearchBudgetDecision(
                allowed=False,
                code="document-already-complete",
                reason=(
                    "The complete indexed evidence for this document is already in "
                    "context. Do not rescan or reopen it. Finish from the evidence "
                    "already collected."
                ),
            )
        if tool_name != "document_fetch":
            return ResearchBudgetDecision(allowed=True)
        if self.document_fetch_calls >= DOCUMENT_FETCH_CALL_LIMIT:
            return ResearchBudgetDecision(
                allowed=False,
                code="document-fetch-budget-exhausted",
                reason=(
                    "The original-document fetch budget for this turn is exhausted. "
                    "Stop retrieving and finish from the evidence already returned."
                ),
            )

        offset = max(chunk_offset, 0)
        windows = self.document_fetch_windows.get(document_id, []) if document_id else []
        if windows:
            previous_offsets = {start for start, _end in windows}
            adjacent_offsets = {end for _start, end in windows}
            if offset in previous_offsets:
                return ResearchBudgetDecision(
                    allowed=False,
                    code="duplicate-document-window",
                    reason=(
                        "This document window was already fetched. Do not reopen it. "
                        "Use the evidence already returned or run one targeted "
                        "full-text search for the requested row."
                    ),
                )
            if offset not in adjacent_offsets:
                return ResearchBudgetDecision(
                    allowed=False,
                    code="distant-document-offset",
                    reason=(
                        "Do not guess a distant chunk offset in the same document. "
                        "Use a full-text/raw-content or in-document search tool for "
                        "the exact row; otherwise finish from the evidence already "
                        "collected."
                    ),
                )
            if not allow_sequential_window:
                return ResearchBudgetDecision(
                    allowed=False,
                    code="filing-sequential-scan",
                    reason=(
                        "Do not page sequentially through a long filing. Load its raw "
                        "content once and run a targeted in-document search for the "
                        "requested field or table row, then finish from that evidence."
                    ),
                )

        # Reserve the budget before execution so parallel calls cannot
        # oversubscribe it. A failed fetch still consumed the turn budget.
        self.document_fetch_calls += 1
        if document_id:
            self.document_fetch_windows.setdefault(document_id, []).append(
                (offset, offset + DOCUMENT_FETCH_CHUNK_LIMIT)
            )
        return ResearchBudgetDecision(
            allowed=True,
            chunk_limit=(
                DOCUMENT_FETCH_CHUNK_LIMIT
                if requested_chunk_limit != DOCUMENT_FETCH_CHUNK_LIMIT
                else requested_chunk_limit
            ),
        )

    def mark_document_complete(self, document_id: str) -> None:
        if document_id:
            self.complete_document_ids.add(document_id)


def prioritize_discovery_documents(
    documents: Sequence[Mapping[str, Any]],
    *,
    tool_name: str,
) -> list[dict[str, Any]]:
    """Prefer one original transcript per fiscal period before alternates.

    Transcript providers commonly interleave a call transcript, its slide deck,
    and duplicate records.  A fixed result window can then expose only two
    quarters even when the provider returned four.  Preserve provider period
    order, choose the most original call record for every distinct period, and
    append alternates only after period coverage is complete.
    """

    copied = [dict(document) for document in documents]
    simple_name = str(tool_name).rsplit("__", 1)[-1]
    if simple_name not in TRANSCRIPT_DISCOVERY_TOOLS:
        return copied

    grouped: dict[tuple[str, str], list[tuple[int, dict[str, Any]]]] = {}
    ungrouped: list[tuple[int, dict[str, Any]]] = []
    for index, document in enumerate(copied):
        metadata = document.get("metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        fiscal_year = str(metadata.get("fiscal_year") or "").strip().upper()
        fiscal_quarter = str(metadata.get("fiscal_quarter") or "").strip().upper()
        if not fiscal_year and not fiscal_quarter:
            ungrouped.append((index, document))
            continue
        grouped.setdefault((fiscal_year, fiscal_quarter), []).append((index, document))

    selected: list[tuple[int, int, dict[str, Any]]] = []
    selected_ids: set[int] = set()
    for (_fiscal_year, fiscal_quarter), candidates in grouped.items():
        best = min(
            candidates,
            key=lambda item: (_transcript_original_rank(item[1]), item[0]),
        )
        period_rank = 0 if fiscal_quarter in {"Q1", "Q2", "Q3", "Q4"} else 1
        selected.append((period_rank, best[0], best[1]))
        selected_ids.add(id(best[1]))
    selected.extend((2, index, document) for index, document in ungrouped)
    selected.sort(key=lambda item: (item[0], item[1]))

    alternates = [
        (index, document)
        for index, document in enumerate(copied)
        if id(document) not in selected_ids
        and not any(document is ungrouped_doc for _i, ungrouped_doc in ungrouped)
    ]
    prioritized = [document for _rank, _index, document in selected]
    return [*prioritized, *(document for _index, document in alternates)]


def _transcript_original_rank(document: Mapping[str, Any]) -> int:
    metadata = document.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    report_type = str(metadata.get("report_type") or document.get("report_type") or "")
    title = str(document.get("title") or "").casefold()
    if report_type == "2" or (
        "transcript" in title and "presentation" not in title and "slide" not in title
    ):
        return 0
    if report_type == "3" or "presentation" in title or "slide" in title:
        return 2
    return 1
