"""SessionOrchestrator — manages Runtime lifecycle around each session's cwd.

Transport-agnostic orchestration layer. WebSocket, REST, and CLI all delegate
to this class for runtime caching, turn execution, interrupt handling, and
cleanup.

Sessions are self-sufficient: each carries its own working directory
(``session.cwd``) and embedded agent snapshot (``session.agent_config``);
this orchestrator does not create or own the directory beyond seeding the
``.claude/CLAUDE.md`` stub.
"""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import json
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from src.core import recovery
from src.core.agent_config import AgentConfig
from src.core.citation import (
    CitationGuard,
    EvidenceRegistry,
    compact_citation_tool_content,
    private_citation_tool_content,
)
from src.core.citation_repair import (
    CITATION_CLAIM_PATCH_VERSION,
    apply_citation_claim_patch,
    repairable_claim_ids,
)
from src.core.citation_research_budget import is_stable_general_knowledge_query
from src.core.claim_audit import extract_claims
from src.core.claim_evidence_resolution import resolve_claim_evidence
from src.core.events import Event, EventSink, GlobalEventTap
from src.core.output_contract import parse_output_contract
from src.core.prompt_builder import wrap_for_mode
from src.core.runtime_port import RuntimePort
from src.core.session_approval_cache import SessionApprovalCache, SessionRule
from src.core.session_bus import SessionEventBus
from src.core.store_port import StorePort
from src.core.time_utils import now_ms
from src.core.types import (
    BARE_COMPLETION_METADATA_KEY,
    Error,
    Message,
    Session,
    UserMessage,
)
from src.core.workspace import bootstrap_session_workspace

# Per-session callable injected into runtimes that wire ``approve_for_session``.
# Closes over (session_id, cache, runtime.approval_rule_matcher) so the
# runtime can check the cache without depending on SessionOrchestrator.
# Return value: matching ``SessionRule`` on hit, ``None`` on miss.
# See ``docs/design/approve-for-session.md`` §3.3 for the cache-hit flow.
SessionRuleFinder = Callable[[str, str, dict[str, Any], dict[str, Any]], "SessionRule | None"]
CitationRepairRefreshHook = Callable[[str, str], Awaitable[bool]]

logger = logging.getLogger(__name__)


def _citation_output_scope_context(user_prompt: str) -> str:
    """Return a small host contract for multi-period sourced answers."""

    contract = parse_output_contract(user_prompt)
    count = contract.requested_period_count
    if count is None or count < 2:
        return ""
    return (
        "Host-enforced multi-period answer contract:\n"
        f"- The final answer must contain {count} distinct period sections, one for "
        "each requested source period.\n"
        "- Cover the user's requested topics inside every period. If a topic is "
        "absent from that period's source, state that gap inside the same period.\n"
        "- Do not finish with only a cross-period recap, retrieval note, coverage "
        "limitation, or statement that the work is complete. Publish the actual "
        "period-by-period answer.\n"
        "- Once the required periods have evidence, stop searching and compose the "
        "answer from the collected evidence handles."
    )

_CITATION_REPAIR_PROMPT = f"""The sealed draft has claim-local citation issues.
Do not rewrite the answer. Return only a JSON claim patch with this exact shape:
{{"version":"{CITATION_CLAIM_PATCH_VERSION}","patches":[{{"claimId":"...","replacementText":"...","evidenceHandles":["ev_..."]}}]}}

Rules:
- Patch only claim ids listed in claimIssues. Untouched answer text is owned by
  the host and will remain byte-for-byte unchanged.
- replacementText replaces exactly the claim's sourceText. For a table-cell
  claim, return only the replacement cell content, not its row/header labels.
  It contains no Markdown citation link, evidence handle, source list,
  validation code, or commentary. The host attaches validated handles.
- Use only exact handles from candidateEvidence. Never invent, copy, merge, or
  guess a handle.
- Do not call tools. Research is complete and the repair pass is intentionally
  isolated from tools and conversation history. If candidateEvidence cannot
  support a claim, replace only that claim with a concise user-facing coverage
  limitation.
- Correct a value only when trusted evidence supports the correction. Never use
  a proxy metric, different entity, period, document, or derived calculation as
  if it were the requested claim.
- Preserve the claim's requested field, entity, period, unit and presentation.
- Return JSON only. A full rewritten answer, prose, empty response, unknown
  claim id, or unknown handle is rejected and the sealed draft is published.
"""

_MAX_CITATION_REPAIR_CLAIMS = 12
# A claim patch contains at most twelve explicit issues.  Starting a hidden
# model pass for a draft with more failures cannot repair the complete set in
# one bounded attempt; it only adds latency/cost and tempts the model to hollow
# out the answer.  Publish that useful draft with advisory citation state and
# let the next turn refine it instead.
_MAX_CITATION_REPAIR_PROBLEM_CLAIMS = _MAX_CITATION_REPAIR_CLAIMS
_MAX_CITATION_REPAIR_EVIDENCE = 24
_MAX_CITATION_REPAIR_DRAFT_CHARS = 40_000
_MAX_CITATION_REPAIR_CONTEXT_CHARS = 160_000
_REPAIRABLE_CLAIM_ISSUE_CODES = {
    "claim_without_citation",
    "numeric_claim_without_citation",
    "date_claim_without_citation",
    "numeric_unit_missing",
    "numeric_period_or_as_of_missing",
    "claim_evidence_ambiguous",
    "claim_evidence_conflict",
    "claim_source_entity_conflict",
    "claim_evidence_mismatch",
    "claim_partially_supported",
}
_ACTIONABLE_REPAIR_ISSUE_CODES = {
    "claim_evidence_conflict",
    "claim_source_entity_conflict",
}

_INTERNAL_CITATION_PROSE_RE = re.compile(
    r"(?:"
    r"\bevidence\s*handles?\b|"
    r"\bcandidate\s+evidence\b|"
    r"\bevidenceHandle\b|"
    r"\bcitation\s*ids?\b|"
    r"\bcitationId\b|"
    r"\bclaimId\b|"
    r"\bpolicyRevision\b|"
    r"\bissueCodes?\b|"
    r"valuz\.quality-claim\.invalid|"
    r"_valuz_evidence|"
    r"\[UNSOURCED\]|"
    r"\[UNVERIFIED(?::[^\]]*)?\]|"
    r"(?:证据|引用).{0,12}(?:句柄|记录|凭证|绑定|协议)|"
    r"(?:句柄|记录|凭证).{0,12}(?:证据|引用)|"
    r"合规绑定|经认证的引用|可引用来源|行内引用|"
    r"嵌套(?:财务)?(?:子)?字段|工具原始返回"
    r")",
    re.IGNORECASE,
)
_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_INTERNAL_HANDOFF_RE = re.compile(
    r"^\s*##\s+SESSION INTENT\b[\s\S]*?^##\s+SUMMARY\b"
    r"[\s\S]*?^##\s+ARTIFACTS\b[\s\S]*?^##\s+NEXT STEPS\b",
    re.IGNORECASE | re.MULTILINE,
)
_LEADING_PROGRESS_RE = re.compile(
    r"^(?:"
    r".{0,48}(?:找到(?:了)?|均?已找到|已取得|已获取|已充分|已齐全)"
    r".{0,48}(?:原文|资料|来源|数据|财报|年报|证据|句柄|chunk|结果|报告).{0,80}|"
    r"(?:需要|需|还要|必须).{0,48}(?:从|先|再)?.{0,32}"
    r"(?:获取|读取|检索|查找|查证|核验).{0,48}"
    r"(?:原文|资料|来源|数据|财报|年报|证据|句柄|chunk).{0,80}|"
    r".{0,48}(?:原文|资料|来源|数据|财报|年报|证据|chunk|结果|报告)"
    r".{0,64}(?:找到(?:了)?|均?已找到|已取得|已获取|数据一致|现引用).{0,80}|"
    r"(?:现在|接下来|下面)(?:开始|将|直接)?.{0,32}"
    r"(?:读取|检索|查找|整合|汇总|整理|撰写|生成|计算|核验|验证).{0,160}|"
    r"(?:均?)?已(?:完整)?(?:阅读|查看|检查|核对).{0,64}"
    r"(?:现在)?(?:开始)?(?:整理|给出|输出).{0,80}|"
    r"(?:基于|根据).{0,48}(?:已收集|已获取|已有).{0,48}(?:答案|结果|信息).{0,80}|"
    r"(?:现已|已经|已).{0,24}(?:收集|汇总|整合).{0,24}"
    r"(?:数据|资料|来源|信息).{0,64}(?:以下|下面).{0,32}"
    r"(?:结果|答案).{0,16}|"
    r"(?:现在)?(?:可以|将要)?给出.{0,48}(?:答案|结果).{0,80}|"
    r"以下是.{0,32}(?:完整)?答案\s*[:：]?|"
    r"(?:I|we)(?: now)?(?: have|'ve)? (?:found|retrieved|collected).{0,80}|"
    r"(?:I|we)(?: now)?(?: have|'ve) (?:all|enough|the needed).{0,32}"
    r"(?:data|information|sources?|evidence).{0,80}|"
    r"(?:let me|I(?: will|'ll)|we(?: will|'ll))\s+"
    r"(?:compile|consolidate|summari[sz]e|prepare|write).{0,80}"
    r")$",
    re.IGNORECASE,
)
_LEADING_PROGRESS_INTERNAL_RE = re.compile(
    r"(?:evidence\s*handles?|evidenceHandle|证据句柄|doc[_ -]?id|"
    r"chunk\s*`?[A-Za-z0-9_-]{6,}`?|(?<![A-Za-z0-9_/])`?ev_[A-Za-z0-9_-]{8,}`?)",
    re.IGNORECASE,
)
_EXPLICIT_EXCERPT_REQUEST_RE = re.compile(
    r"(?:摘录|逐字(?:引用|摘录)|给出.{0,16}原文(?:段落|内容)|"
    r"展示.{0,16}原文(?:段落|内容)|\bverbatim\b|"
    r"\bquote\b.{0,24}\b(?:passage|excerpt|text)\b|"
    r"\bshow\b.{0,24}\bexcerpt\b)",
    re.IGNORECASE,
)
_ORIGINAL_SOURCE_CITATION_REQUEST_RE = re.compile(
    r"(?:引用.{0,20}原文|根据.{0,20}原文.{0,20}引用|"
    r"cite.{0,24}(?:filing|report|original source))",
    re.IGNORECASE,
)
_STRICT_OUTPUT_SCOPE_RE = re.compile(
    r"(?:只列出|只输出|不要添加其他内容|仅列出|仅输出|"
    r"只用\s*[一二两三四五六七八九十\d]+\s*行|"
    r"\bonly\s+(?:list|output|return)\b|\bnothing\s+else\b)",
    re.IGNORECASE,
)
_STRICT_TRAILING_SOURCE_NOTE_RE = re.compile(
    r"\n{2,}(?:数据|资料|信息)?来源\s*[:：][\s\S]*$",
    re.IGNORECASE,
)
_STRICT_TRAILING_SOURCE_EXPLANATION_RE = re.compile(
    r"\n{2,}(?:以上|上述|这些|本表).{0,32}(?:数据|内容).{0,48}"
    r"(?:引用|来自|来源于|取自)[\s\S]*$",
    re.IGNORECASE,
)
_NO_RETRIEVAL_REQUEST_RE = re.compile(
    r"(?:不需要|无需|不用|不必).{0,16}(?:查询|检索|搜索|查找)|"
    r"\bwithout\s+(?:searching|retrieval|looking\s+up)\b",
    re.IGNORECASE,
)
_STRICT_MARKDOWN_TABLE_REQUEST_RE = re.compile(
    r"(?:只|仅).{0,16}(?:输出|返回|列出).{0,16}(?:Markdown\s*)?表格|"
    r"\bonly\s+(?:output|return)\b.{0,24}\bmarkdown\s+table\b",
    re.IGNORECASE,
)
_PERIOD_BY_PERIOD_REQUEST_RE = re.compile(
    r"(?:按|逐)(?:季度|月份|月度|年度|年份|期间|期次)|"
    r"\b(?:quarter|period)[ -]by[ -](?:quarter|period)\b",
    re.IGNORECASE,
)
_EXPLICIT_CROSS_PERIOD_RECAP_RE = re.compile(
    r"(?:跨(?:季度|月份|年度|期间).{0,12}(?:趋势|概览|对比|比较|汇总)|"
    r"(?:趋势|概览|对比|比较|汇总).{0,12}(?:表|表格)|"
    r"\b(?:cross[ -]period|comparison|trend|overview|summary)[ -]?(?:table|view)?\b)",
    re.IGNORECASE,
)
_PERIOD_HEADING_RE = re.compile(
    r"^\s*#{1,6}\s+.*(?:FY\s*\d{2,4}\s*Q[1-4]|Q[1-4]\s*FY\s*\d{2,4}|"
    r"\d{4}\s*年?\s*(?:Q[1-4]|第?[一二三四1-4]季度))",
    re.IGNORECASE,
)
_CROSS_PERIOD_RECAP_HEADING_RE = re.compile(
    r"^\s*#{1,6}\s+.*(?:跨(?:季度|月份|年度|期间)|"
    r"(?:趋势|对比|比较|汇总).{0,12}(?:概览|总览|表)|"
    r"(?:核心|主要|整体).{0,12}(?:主线|趋势|结论).{0,8}(?:归纳|总结|汇总)|"
    r"(?:横向|纵向|整体|综合)?\s*(?:对比|比较|小结|总结|归纳)\s*$|"
    r"(?:cross[ -]period|comparison|trend|overview))",
    re.IGNORECASE,
)
_REPAIR_RELEVANCE_TRANSLATION = str.maketrans(
    {
        "譽": "誉",
        "淨": "净",
        "潤": "润",
        "額": "额",
        "國": "国",
        "際": "际",
        "財": "财",
        "報": "报",
        "準": "准",
        "則": "则",
        "經": "经",
        "調": "调",
        "賬": "账",
        "幣": "币",
        "為": "为",
        "團": "团",
        "產": "产",
        "總": "总",
        "佔": "占",
        "約": "约",
    }
)
_REPAIR_RELEVANCE_STOP_TERMS = {
    "分析",
    "报告",
    "报告期",
    "财报",
    "年度",
    "金额",
    "数字",
    "两个",
    "单位",
    "人民币",
    "只列出",
    "只输出",
    "注明",
    "不要",
    "生成",
    "图表",
    "扩展",
}
_SCOPE_LEADIN_RE = re.compile(
    r"^(?:(?:以下|下面|根据|据).{0,120}(?:信息|数据|结果|内容|项目|项|原文|记录)"
    r"(?:\s*[（(][^\n]{0,80}[）)])?\s*[:：]?|"
    r"在.{0,80}(?:读取|检索|查看|核对).{0,80}(?:原文|资料|文档).{0,80}"
    r"(?:后|之后).{0,24}(?:确认|得到|整理).{0,24}(?:情况|结果|如下)\s*[:：]?|"
    r"(?:所有|以上)?.{0,40}(?:已核实|已确认).{0,80}以下是.{0,120}(?:数据|结果|项目|项)\s*[:：]?|"
    r"(?:note:\s*)?[\s\S]{0,260}(?:now\s+)?(?:i|we)\s+have\s+"
    r"(?:everything|all(?:\s+the)?\s+(?:data|information|evidence))\s+needed[.!]?)\s*$",
    re.IGNORECASE,
)
_STRICT_TRAILING_RECAP_RE = re.compile(
    r"\n{2,}(?:\*\*)?(?:结论|总结)(?:\*\*)?\s*[:：][\s\S]*$",
    re.IGNORECASE,
)
_STRICT_TRAILING_ADVICE_RE = re.compile(
    r"\n{2,}(?:如需|若需|建议|需要的话)[\s\S]*$",
    re.IGNORECASE,
)
_RETRIEVAL_INTERNAL_TERM_RE = re.compile(
    r"(?:(?:\b(?:excerpt|chunks?)\b|\bmiddle omitted\b)|"
    r"(?:indexed|index(?:ed)? content)|"
    r"(?:检索|索引|来源)(?:内容|结果|块)|"
    r"(?:文档|原文|文本|证据|上下文).{0,80}(?:分块|截断|压缩)|"
    r"(?:工具|系统).{0,12}(?:返回|省略|截断|限制)|"
    r"(?:节选|句子|内容|文本).{0,12}(?:截断|省略|不完整|未完整)|"
    r"(?:返回的?)?(?:全部)?\s*\d+\s*个?(?:文本)?段落|"
    r"(?:检索到的?)?(?:电话会|文档|原文).{0,160}(?:未完整(?:收录|覆盖)|覆盖不完整)|"
    r"(?:检索|扫描|检查|读取).{0,32}(?:全部|全文|所有|完整).{0,24}"
    r"(?:内容|原文|文档))",
    re.IGNORECASE,
)
_NEGATIVE_RETRIEVAL_ABSENCE_RE = re.compile(
    r"(?:未(?:出现|涉及|披露|找到|发现|给出|提供|确认|能完整获取)|"
    r"没有(?:出现|披露|找到|发现|给出|提供)|无法(?:引用|确认)|"
    r"\b(?:not disclosed|not found|not available|unable to cite)\b)",
    re.IGNORECASE,
)
_INCOMPLETE_SOURCE_COVERAGE_RE = re.compile(
    r"(?:未完整(?:收录|覆盖)|覆盖不完整|未能完整获取)",
    re.IGNORECASE,
)
_BARE_CITATION_BLOCK_RE = re.compile(
    r"(?m)^[ \t]*(?P<citation>\[[^\]]+\]\((?:evidence|citation)://[^)]+\))[ \t]*$",
    re.IGNORECASE,
)
_STANDALONE_BOLD_LABEL_RE = re.compile(r"^\s*\*\*[^*\n]{1,120}\*\*\s*$")
_BOLD_LABEL_PREFIX_RE = re.compile(r"^\s*\*\*[^*\n]{1,120}\*\*(?:\s*[:：])?")
_RETRIEVAL_INTERNAL_SENTENCE_RE = re.compile(
    r"[^。！？!?\n]*(?:(?:\b(?:excerpt|chunks?)\b|\bmiddle omitted\b)|"
    r"(?:indexed|index(?:ed)? content)|"
    r"(?:检索|索引|来源)(?:内容|结果|块)|"
    r"(?:文档|原文|文本|证据|上下文).{0,80}(?:分块|截断|压缩)|"
    r"(?:工具|系统).{0,12}(?:返回|省略|截断|限制)|"
    r"(?:节选|句子|内容|文本).{0,12}(?:截断|省略|不完整|未完整)|"
    r"(?:返回的?)?(?:全部)?\s*\d+\s*个?(?:文本)?段落|"
    r"(?:检索到的?)?(?:电话会|文档|原文).{0,160}(?:未完整(?:收录|覆盖)|覆盖不完整)|"
    r"(?:检索|扫描|检查|读取).{0,32}(?:全部|全文|所有|完整).{0,24}"
    r"(?:内容|原文|文档))"
    r"[^。！？!?\n]*[。！？!?]?[*_]{0,3}",
    re.IGNORECASE,
)
_RETRIEVAL_ABSENCE_PREFIX_RE = re.compile(
    r"(?:经过|已)?(?:完整)?(?:扫描|检索|检查|读取).{0,48}"
    r"(?:chunks?|分块).{0,24}(?:文稿|原文|文档)?中?未找到\s*",
    re.IGNORECASE,
)
_INCOMPLETE_DISCLOSURE_RE = re.compile(
    r"(?:电话会|文档)?原文.{0,160}?内容未完整披露",
    re.IGNORECASE,
)
_LABELED_RETRIEVAL_ABSENCE_RE = re.compile(
    r"(?P<label>(?:\*\*)?[^。！？!?\n]{1,80}?(?:\*\*)?\s*[:：])"
    r"(?P<body>"
    r"(?=[^。！？!?\n]*(?:未找到|未披露|无法确认|未能确认))"
    r"(?=[^。！？!?\n]*(?:excerpt|chunks?|middle omitted|分块|截断|"
    r"节选|工具|系统|检索|扫描|检查|读取|未完整披露))"
    r"[^。！？!?\n]*"
    r")[。！？!?]?",
    re.IGNORECASE,
)
_TRAILING_RESTATEMENT_RE = re.compile(
    r"\n\s*(?:即|换算后|简而言之|结论)\s*[:：]?\s*(?P<answer>\S[\s\S]*?)\s*$",
    re.IGNORECASE,
)
_EXPLANATION_REQUEST_RE = re.compile(
    r"(?:解释|说明.{0,12}(?:含义|意义)|解读|通俗.{0,8}(?:说明|解释)|"
    r"\b(?:explain|interpret|what does .{0,24} mean)\b)",
    re.IGNORECASE,
)
_INLINE_EVIDENCE_LINK_RE = re.compile(
    r"\[[^\]\n]{0,240}\]\((?:evidence|citation)://[^)\n]+\)",
    re.IGNORECASE,
)
_DISPLAY_NUMBER_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")
_APPENDED_SOURCE_EXCERPT_RE = re.compile(
    r"\n{2,}(?:#{1,6}\s*)?(?:(?:年报|年度报告|报告|文件|来源)\s*)?"
    r"原文(?:\s*[（(][^\n]{0,120}[）)])?\s*[:：]\s*(?:\n|$)",
    re.IGNORECASE,
)


def _strip_leading_assistant_progress(text: str) -> str:
    """Remove model worklog lines that precede the actual user answer."""

    blocks = re.split(r"\n[ \t]*\n", text)
    # Models sometimes introduce a technical list with an innocent-looking
    # sentence ("the annual report has two disclosures"), put raw evidence
    # handles in the next block, then add another transition before the real
    # cited answer.  Once a raw handle appears near the start, treat the whole
    # prefix as one worklog and resume at the first user-facing citation.
    progress_probes = [_INLINE_EVIDENCE_LINK_RE.sub("", block) for block in blocks]
    internal_index = next(
        (
            i
            for i, block in enumerate(progress_probes[:6])
            if _LEADING_PROGRESS_INTERNAL_RE.search(block)
        ),
        None,
    )
    if internal_index is not None:
        cited_answer_index = next(
            (
                i
                for i in range(internal_index + 1, len(blocks))
                if re.search(r"\]\((?:evidence|citation)://", blocks[i], re.IGNORECASE)
            ),
            None,
        )
        if cited_answer_index is not None:
            cleaned = "\n\n".join(blocks[cited_answer_index:]).strip()
            return cleaned or text.strip()

    # A common failed-research shape is: worklog -> provisional bullet recap
    # -> horizontal rule -> "以下为最终答案" -> actual cited answer.  The
    # recap can contain ordinary factual prose, so line-by-line progress
    # matching alone stops too early.  Once an explicit worklog prefix and
    # answer boundary both exist, discard the whole provisional section.
    has_progress_prefix = any(
        _LEADING_PROGRESS_INTERNAL_RE.search(block)
        or any(
            _LEADING_PROGRESS_RE.search(line.strip()) for line in block.splitlines() if line.strip()
        )
        for block in progress_probes[:3]
    )
    if has_progress_prefix:
        for boundary_index, block in enumerate(blocks[:10]):
            if block.strip() not in {"---", "***", "___"}:
                continue
            if boundary_index + 1 >= len(blocks):
                continue
            transition = blocks[boundary_index + 1].strip()
            if re.match(
                r"^(?:以下|下面).{0,64}(?:答案|信息|结果).{0,64}[:：]?$",
                transition,
                re.IGNORECASE,
            ):
                cleaned = "\n\n".join(blocks[boundary_index + 2 :]).strip()
                return cleaned or text.strip()

    index = 0
    removed = False
    while index < len(blocks):
        stripped = blocks[index].strip()
        progress_probe = progress_probes[index].strip()
        if not stripped:
            index += 1
            continue
        if stripped in {"---", "***", "___"} and removed:
            index += 1
            continue
        lines = [line.strip() for line in progress_probe.splitlines() if line.strip()]
        if _LEADING_PROGRESS_INTERNAL_RE.search(progress_probe) or any(
            _LEADING_PROGRESS_RE.search(line) for line in lines
        ):
            removed = True
            index += 1
            continue
        break
    if not removed:
        return text
    while index < len(blocks) and blocks[index].strip() in {"", "---", "***", "___"}:
        index += 1
    cleaned = "\n\n".join(blocks[index:]).strip()
    # A progress-only assistant block is not an answer. Returning the original
    # text here leaked retrieval narration (and sometimes protocol vocabulary)
    # as a separate user-visible message when a run was cancelled or repaired.
    return cleaned


def _strip_unrequested_source_excerpt(text: str, user_prompt: str) -> str:
    """Collapse an ordinary citation request to one canonical cited answer."""

    if not _ORIGINAL_SOURCE_CITATION_REQUEST_RE.search(user_prompt):
        return text
    if _EXPLICIT_EXCERPT_REQUEST_RE.search(user_prompt):
        return text
    restatement = _TRAILING_RESTATEMENT_RE.search(text)
    if restatement is not None:
        answer = restatement.group("answer").strip()
        if "citation://" in answer or "evidence://" in answer:
            return answer
    source_section = _APPENDED_SOURCE_EXCERPT_RE.search(text)
    if source_section is not None:
        answer = text[: source_section.start()].strip()
        if "citation://" in answer or "evidence://" in answer:
            return answer
    return text


def _strip_unrequested_derived_restatement(text: str, user_prompt: str) -> str:
    """Keep one cited formula instead of a duplicate calculation recap."""

    if _EXPLANATION_REQUEST_RE.search(user_prompt):
        return text
    blocks = re.split(r"\n[ \t]*\n", text)
    if len(blocks) >= 2:
        trailing = blocks[-1].strip()
        trailing_citation = _INLINE_EVIDENCE_LINK_RE.search(trailing)
        formula_index = next(
            (index for index in range(len(blocks) - 2, -1, -1) if "=" in blocks[index]),
            None,
        )
        if trailing_citation is not None and formula_index is not None:
            leading_numbers = _DISPLAY_NUMBER_RE.findall(trailing[: trailing_citation.start()])
            formula_numbers = {
                value.replace(",", "")
                for value in _DISPLAY_NUMBER_RE.findall(blocks[formula_index])
            }
            displayed_result = leading_numbers[-1].replace(",", "") if leading_numbers else ""
            if displayed_result and displayed_result in formula_numbers:
                citation = trailing_citation.group(0)
                if _INLINE_EVIDENCE_LINK_RE.search(blocks[formula_index]) is None:
                    blocks[formula_index] = f"{blocks[formula_index].rstrip()}  {citation}"
                return "\n\n".join(blocks[:-1]).strip()
    restatement = _TRAILING_RESTATEMENT_RE.search(text)
    if restatement is None:
        return text
    answer = restatement.group("answer")
    prefix = text[: restatement.start()].rstrip()
    if _INLINE_EVIDENCE_LINK_RE.search(answer):
        return text
    if "=" not in prefix or _INLINE_EVIDENCE_LINK_RE.search(prefix) is None:
        return text
    return prefix


def _strip_unrequested_cross_period_recap(text: str, user_prompt: str) -> str:
    """Keep the requested period sequence as the single answer structure.

    Models sometimes repeat an already complete period-by-period answer in a
    trailing comparison table.  Besides wasting space, that recap creates a
    second set of claims whose citations frequently detach from the supported
    period-local statements.  Remove only an explicit trailing recap section
    when the user requested a period sequence and did not request that extra
    comparison/trend view.
    """

    if not _PERIOD_BY_PERIOD_REQUEST_RE.search(user_prompt):
        return text
    if _EXPLICIT_CROSS_PERIOD_RECAP_RE.search(user_prompt):
        return text
    lines = text.splitlines()
    period_headings = 0
    for index, line in enumerate(lines):
        if _PERIOD_HEADING_RE.match(line):
            period_headings += 1
            continue
        if period_headings < 2 or not _CROSS_PERIOD_RECAP_HEADING_RE.match(line):
            continue
        kept = lines[:index]
        while kept and kept[-1].strip() in {"", "---", "***", "___"}:
            kept.pop()
        return "\n".join(kept).strip()
    return text


def _strip_unrequested_period_leadin(text: str, user_prompt: str) -> str:
    """Start a period-by-period answer at its first requested period.

    A title, research-method preamble, and synthetic coverage range ahead of
    an otherwise complete period sequence add no answer content and create
    extra uncited claims. Keep them only when the user explicitly requested a
    cross-period overview; otherwise the first period heading is the stable
    answer boundary.
    """

    if not _PERIOD_BY_PERIOD_REQUEST_RE.search(user_prompt):
        return text
    if _EXPLICIT_CROSS_PERIOD_RECAP_RE.search(user_prompt):
        return text
    lines = text.splitlines()
    heading_indexes = [
        index for index, line in enumerate(lines) if _PERIOD_HEADING_RE.match(line)
    ]
    if len(heading_indexes) < 2 or heading_indexes[0] == 0:
        return text
    return "\n".join(lines[heading_indexes[0] :]).strip()


def _strip_strict_scope_leadin(text: str, user_prompt: str) -> str:
    """Remove a presentation-only preamble when the user requested only rows."""

    if not _STRICT_OUTPUT_SCOPE_RE.search(user_prompt):
        return text
    # Claude can occasionally emit an English provisional answer and then the
    # requested Chinese answer in the same final block.  For a strict Chinese
    # output contract, discard that leading worklog/duplicate only when the
    # prefix contains no Chinese and explicitly narrates its own progress.
    if _CJK_RE.search(user_prompt):
        all_blocks = re.split(r"\n[ \t]*\n", text)
        first_cjk_index = next(
            (index for index, block in enumerate(all_blocks) if _CJK_RE.search(block)),
            None,
        )
        if first_cjk_index:
            prefix = "\n\n".join(all_blocks[:first_cjk_index]).strip()
            if not _CJK_RE.search(prefix) and re.search(
                r"\b(?:I now have|I have|I've|I found|Let me)\b",
                prefix,
                re.IGNORECASE,
            ):
                text = "\n\n".join(all_blocks[first_cjk_index:]).strip()
    blocks = re.split(r"\n[ \t]*\n", text)
    # A model can emit a provisional sourced recap before its actual strict
    # answer, then introduce the canonical rows with another presentation-only
    # lead-in.  Prefer the last such lead-in so the provisional recap and any
    # duplicated quote disappear together.  This is limited to an explicit
    # strict-output request and never removes a cited block by itself.
    leadin_indexes = [
        index
        for index, block in enumerate(blocks[:-1])
        if len(block.strip()) <= 320
        and not re.search(
            r"\]\((?:evidence|citation)://",
            block,
            re.IGNORECASE,
        )
        and _SCOPE_LEADIN_RE.match(block.strip())
    ]
    if leadin_indexes:
        text = "\n\n".join(blocks[leadin_indexes[-1] + 1 :]).strip()
    text = _STRICT_TRAILING_RECAP_RE.sub("", text).strip()
    text = _STRICT_TRAILING_ADVICE_RE.sub("", text).strip()
    text = _STRICT_TRAILING_SOURCE_NOTE_RE.sub("", text).strip()
    text = _STRICT_TRAILING_SOURCE_EXPLANATION_RE.sub("", text).strip()
    text = _strict_markdown_tables_only(text, user_prompt)
    text = _strip_strict_table_trailing_blocks(text, user_prompt)
    return _enforce_requested_line_count(text, user_prompt)


def _strip_strict_table_trailing_blocks(text: str, user_prompt: str) -> str:
    """Keep one complete table as the sole strict-scope answer structure."""

    if not _STRICT_OUTPUT_SCOPE_RE.search(user_prompt):
        return text
    if _EXPLANATION_REQUEST_RE.search(user_prompt):
        return text
    lines = text.splitlines()
    table_ranges: list[tuple[int, int]] = []
    index = 0
    while index < len(lines):
        if not lines[index].strip().startswith("|"):
            index += 1
            continue
        start = index
        while index < len(lines) and lines[index].strip().startswith("|"):
            index += 1
        block = lines[start:index]
        if len(block) >= 3 and re.fullmatch(
            r"\s*\|(?:\s*:?-{3,}:?\s*\|)+\s*",
            block[1],
        ):
            table_ranges.append((start, index - 1))
    if len(table_ranges) != 1:
        return text
    _start, end = table_ranges[0]
    if not any(line.strip() for line in lines[end + 1 :]):
        return text
    # A strict "only these fields" request does not authorize a prose recap
    # after the complete table.  Removing it also prevents duplicate uncited
    # formulas from triggering a costly repair pass.
    return "\n".join(lines[: end + 1]).strip()


def _enforce_requested_line_count(text: str, user_prompt: str) -> str:
    """Render a table's data rows as the exact number of requested lines."""

    contract = parse_output_contract(user_prompt)
    line_count = contract.requested_line_count
    if line_count is None or line_count < 1 or contract.table_only:
        return text
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) == line_count and not any(line.startswith("|") for line in lines):
        return "  \n".join(lines)
    table_lines = [line for line in lines if line.startswith("|") and line.endswith("|")]
    if len(table_lines) < line_count + 2:
        return text
    headers = [cell.strip() for cell in table_lines[0].strip("|").split("|")]
    data_lines = [
        line
        for line in table_lines[1:]
        if not all(re.fullmatch(r":?-{2,}:?", cell.strip()) for cell in line.strip("|").split("|"))
    ]
    if len(data_lines) < line_count:
        return text
    output: list[str] = []
    for line in data_lines[:line_count]:
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if not cells:
            continue
        fields = [cells[0]]
        fields.extend(
            f"{headers[index]}：{cell}"
            for index, cell in enumerate(cells[1:], start=1)
            if cell and index < len(headers)
        )
        output.append("；".join(fields))
    # A plain newline inside one Markdown paragraph is a soft break and most
    # renderers collapse it to a space.  Preserve the user's explicit visual
    # line contract with CommonMark hard breaks while keeping the records in
    # one compact block.
    return "  \n".join(output) if len(output) == line_count else text


def _strict_markdown_tables_only(text: str, user_prompt: str) -> str:
    """Honor an explicit table-only contract without rewriting table cells."""

    if not _STRICT_MARKDOWN_TABLE_REQUEST_RE.search(user_prompt):
        return text
    lines = text.splitlines()
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.strip().startswith("|"):
            current.append(line)
            continue
        if current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    tables = [
        "\n".join(block).strip()
        for block in blocks
        if len(block) >= 2
        and re.fullmatch(
            r"\s*\|(?:\s*:?-{3,}:?\s*\|)+\s*",
            block[1],
        )
    ]
    return "\n\n".join(tables) if tables else text


def _attach_standalone_citation_lines(text: str) -> str:
    """Attach a citation-only Markdown line to its local rendered claims.

    Models often format a block quote as ``> quote`` followed by
    ``> [source](evidence://...)``. Markdown renders that as one visual quote,
    but the claim auditor sees two line records and previously left the quote
    unsourced. Fold only citation-only lines whose preceding non-empty line is
    itself a block quote; decorative heading citations remain untouched and
    continue through the normal cleanup path.

    A second common shape is a complete Markdown table followed by one line of
    N source links, one per data row.  When the counts match exactly, distribute
    those links to the rows in order.  This is structural binding, not fuzzy
    evidence matching: ambiguous counts or already-cited rows are untouched.
    """

    citation_only = re.compile(
        r"^\s*>\s*(?P<links>(?:\[[^\]\n]{0,240}\]"
        r"\((?:evidence|citation)://[^)\n]+\)\s*)+)$",
        re.IGNORECASE,
    )
    lines = text.splitlines()
    plain_citation_only = re.compile(
        r"^\s*(?P<links>(?:\[[^\]\n]{0,240}\]"
        r"\((?:evidence|citation)://[^)\n]+\)\s*)+)$",
        re.IGNORECASE,
    )
    table_delimiter = re.compile(
        r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$"
    )
    table_bound: list[str] = []
    for line in lines:
        match = plain_citation_only.fullmatch(line)
        links = _INLINE_EVIDENCE_LINK_RE.findall(match.group("links")) if match else []
        previous_index = len(table_bound) - 1
        while previous_index >= 0 and not table_bound[previous_index].strip():
            previous_index -= 1
        if (
            not links
            or previous_index < 0
            or not table_bound[previous_index].strip().startswith("|")
            or not table_bound[previous_index].rstrip().endswith("|")
        ):
            table_bound.append(line)
            continue
        block_start = previous_index
        while block_start > 0 and table_bound[block_start - 1].strip().startswith("|"):
            block_start -= 1
        delimiter_index = next(
            (
                index
                for index in range(block_start, previous_index + 1)
                if table_delimiter.fullmatch(table_bound[index])
            ),
            None,
        )
        data_rows = (
            list(range(delimiter_index + 1, previous_index + 1))
            if delimiter_index is not None
            else []
        )
        if (
            len(data_rows) != len(links)
            or any(_INLINE_EVIDENCE_LINK_RE.search(table_bound[index]) for index in data_rows)
        ):
            table_bound.append(line)
            continue
        for row_index, link in zip(data_rows, links, strict=True):
            row = table_bound[row_index].rstrip()
            table_bound[row_index] = f"{row[:-1].rstrip()} {link} |"
        del table_bound[previous_index + 1 :]
    lines = table_bound
    output: list[str] = []
    for line in lines:
        match = citation_only.fullmatch(line)
        if match is None:
            output.append(line)
            continue
        previous_index = len(output) - 1
        while previous_index >= 0 and not output[previous_index].strip():
            previous_index -= 1
        if previous_index < 0 or not output[previous_index].lstrip().startswith(">"):
            output.append(line)
            continue
        output[previous_index] = (
            f"{output[previous_index].rstrip()} {match.group('links').strip()}"
        )
    # A cited introduction ending in a colon commonly owns the literal block
    # quote that follows. Propagate only to the first uncited quote line; this
    # keeps source attribution local and does not spread citations across
    # ordinary paragraphs.
    for index, line in enumerate(output):
        if not line.lstrip().startswith(">") or _INLINE_EVIDENCE_LINK_RE.search(line):
            continue
        previous_index = index - 1
        while previous_index >= 0 and not output[previous_index].strip():
            previous_index -= 1
        if previous_index < 0 or output[previous_index].lstrip().startswith(">"):
            continue
        previous = output[previous_index].rstrip()
        if not re.search(r"[:：]\s*$", previous):
            continue
        citations = _INLINE_EVIDENCE_LINK_RE.findall(previous)
        if citations:
            output[index] = f"{line.rstrip()} {citations[-1]}"
    return "\n".join(output)


def _strip_unrequested_retrieval_internals(text: str, user_prompt: str) -> str:
    """Hide model narration about evidence transport unless explicitly requested."""

    if _RETRIEVAL_INTERNAL_TERM_RE.search(user_prompt):
        return text
    text = _rewrite_markdown_table_retrieval_absence(text)
    text = _rewrite_labeled_retrieval_absence_lines(text)
    if _STRICT_OUTPUT_SCOPE_RE.search(user_prompt):
        text = _collapse_strict_negative_disclosure_details(text)
    text = _LABELED_RETRIEVAL_ABSENCE_RE.sub(
        _rewrite_labeled_retrieval_absence,
        text,
    )
    text = _INCOMPLETE_DISCLOSURE_RE.sub("原文未披露", text)
    text = _RETRIEVAL_ABSENCE_PREFIX_RE.sub("原文未披露", text)
    cleaned = _RETRIEVAL_INTERNAL_SENTENCE_RE.sub(
        _rewrite_or_remove_retrieval_internal_sentence,
        text,
    )
    if _STRICT_OUTPUT_SCOPE_RE.search(user_prompt):
        cleaned = _BARE_CITATION_BLOCK_RE.sub(
            r"原文未披露具体数字 \g<citation>。",
            cleaned,
        )
    cleaned = re.sub(
        r"([ \t]+)\n",
        lambda match: "  \n" if match.group(1) == "  " else "\n",
        cleaned,
    )
    cleaned = re.sub(r"(?m)^\s*(?:\*+|_+)\s*$\n?", "", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _strip_empty_markdown_labels(text: str) -> str:
    """Remove decorative bold labels that have no body of their own."""

    lines = text.splitlines()
    output: list[str] = []
    for index, line in enumerate(lines):
        if not _STANDALONE_BOLD_LABEL_RE.fullmatch(line):
            output.append(line)
            continue
        next_line = ""
        for candidate in lines[index + 1 :]:
            if candidate.strip():
                next_line = candidate.strip()
                break
        if (
            not next_line
            or _BOLD_LABEL_PREFIX_RE.match(next_line)
            or re.fullmatch(r"-{3,}", next_line)
            or next_line.startswith("#")
        ):
            continue
        output.append(line)
    cleaned = "\n".join(output)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _strip_empty_markdown_tables(text: str) -> str:
    """Remove Markdown table shells that contain no data rows."""

    lines = text.splitlines()
    output: list[str] = []
    index = 0
    while index < len(lines):
        if not lines[index].lstrip().startswith("|"):
            output.append(lines[index])
            index += 1
            continue
        end = index
        while end < len(lines) and lines[end].lstrip().startswith("|"):
            end += 1
        block = lines[index:end]
        separator = block[1] if len(block) >= 2 else ""
        separator_cells = [cell.strip() for cell in separator.strip().strip("|").split("|")]
        is_separator = bool(separator_cells) and all(
            re.fullmatch(r":?-{3,}:?", cell) is not None for cell in separator_cells
        )
        if not (len(block) == 2 and is_separator):
            output.extend(block)
        index = end
    cleaned = "\n".join(output)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _rewrite_or_remove_retrieval_internal_sentence(match: re.Match[str]) -> str:
    value = match.group(0)
    if not _NEGATIVE_RETRIEVAL_ABSENCE_RE.search(value):
        return ""
    if _INCOMPLETE_SOURCE_COVERAGE_RE.search(value):
        return "当前来源未包含具体数字。"
    citations = re.findall(
        r"\s*\[[^\]]+\]\((?:evidence|citation)://[^)]+\)",
        value,
        re.IGNORECASE,
    )
    if not citations:
        return "原文未披露具体数字。"
    return f"原文未披露具体数字 {citations[0].strip()}。"


def _rewrite_markdown_table_retrieval_absence(text: str) -> str:
    """Keep a requested table row while removing retrieval implementation prose."""

    rewritten: list[str] = []
    for line in text.splitlines():
        if not line.lstrip().startswith("|") or line.count("|") < 3:
            rewritten.append(line)
            continue
        cells = line.split("|")
        changed = False
        for index in range(1, len(cells) - 1):
            cell = cells[index]
            if not (
                _NEGATIVE_RETRIEVAL_ABSENCE_RE.search(cell)
                and _RETRIEVAL_INTERNAL_TERM_RE.search(cell)
            ):
                continue
            citations = re.findall(
                r"\s*\[[^\]]+\]\((?:evidence|citation)://[^)]+\)",
                cell,
                re.IGNORECASE,
            )
            citation = citations[0].strip() if citations else ""
            suffix = f" {citation}" if citation else ""
            cells[index] = f" 原文未披露具体数字{suffix}。 "
            changed = True
        rewritten.append("|".join(cells) if changed else line)
    return "\n".join(rewritten)


def _rewrite_labeled_retrieval_absence_lines(text: str) -> str:
    """Normalize a multi-sentence labeled field without losing its citation."""

    rewritten: list[str] = []
    label_pattern = re.compile(
        r"^(?P<label>\s*(?:\*\*)?[^。！？!?\n:：]{1,80}?(?:\*\*)?"
        r"\s*[:：]\s*(?:\*\*)?)(?P<body>.*)$"
    )
    for line in text.splitlines():
        match = label_pattern.match(line)
        if match is None:
            rewritten.append(line)
            continue
        body = match.group("body")
        if not (
            _NEGATIVE_RETRIEVAL_ABSENCE_RE.search(body) and _RETRIEVAL_INTERNAL_TERM_RE.search(body)
        ):
            rewritten.append(line)
            continue
        citations = re.findall(
            r"\s*\[[^\]]+\]\((?:evidence|citation)://[^)]+\)",
            body,
            re.IGNORECASE,
        )
        citation = citations[0].strip() if citations else ""
        suffix = f" {citation}" if citation else ""
        rewritten.append(f"{match.group('label')}原文未披露具体数字{suffix}。")
    return "\n".join(rewritten)


def _collapse_strict_negative_disclosure_details(text: str) -> str:
    """Keep one sourced absence sentence when the user requested only fields."""

    rewritten: list[str] = []
    label_pattern = re.compile(
        r"^(?P<label>\s*(?:[-*+]\s+)?(?:\*\*)?[^。！？!?\n:：]{1,100}?"
        r"(?:\*\*)?\s*[:：]\s*(?:\*\*)?)(?P<body>.*)$"
    )
    for line in text.splitlines():
        match = label_pattern.match(line)
        if match is None or not _NEGATIVE_RETRIEVAL_ABSENCE_RE.search(match.group("body")):
            rewritten.append(line)
            continue
        citations = re.findall(
            r"\s*\[[^\]]+\]\((?:evidence|citation)://[^)]+\)",
            match.group("body"),
            re.IGNORECASE,
        )
        if not citations:
            rewritten.append(line)
            continue
        rewritten.append(f"{match.group('label')}原文未披露具体数字 {citations[0].strip()}。")
    return "\n".join(rewritten)


def _rewrite_labeled_retrieval_absence(match: re.Match[str]) -> str:
    label = match.group("label").strip()
    body = match.group("body")
    citations = re.findall(
        r"\s*\[[^\]]+\]\((?:evidence|citation)://[^)]+\)",
        body,
        re.IGNORECASE,
    )
    citation = citations[-1].strip() if citations else ""
    suffix = f" {citation}" if citation else ""
    return f"{label}原文未披露具体数字{suffix}。"


def _sanitize_citation_repair_prose(text: str) -> str:
    """Remove citation-protocol diagnostics from a repaired user answer.

    The model receives opaque repair metadata so it can bind evidence, but
    that implementation vocabulary is never useful to the end user. Prompt
    rules are the primary control; this block-level filter is the deterministic
    backstop when a model repeats the restricted context in its answer. Safe
    blocks and their citations are preserved instead of replacing the whole
    paid response with a generic failure sentence.
    """

    parts = re.split(r"(\n[ \t]*\n)", text)
    output: list[str] = []
    for part in parts:
        if not part or re.fullmatch(r"\n[ \t]*\n", part):
            if output and output[-1] != "\n\n":
                output.append("\n\n")
            continue
        output.append(_strip_internal_repair_sentences(part))
    return "".join(output).strip()


def _strip_internal_repair_sentences(value: str) -> str:
    """Remove protocol diagnostics while retaining user-facing facts.

    Repair models occasionally append an evidence-handle explanation to the
    same line as a requested numeric result. Dropping the whole paragraph
    erased the paid answer and replaced it with a global warning. Work at the
    sentence/line level and keep a factual prefix when the internal term only
    starts a trailing diagnostic clause.
    """

    output: list[str] = []
    for line in value.splitlines():
        kept: list[str] = []
        for sentence in re.split(
            r"(?<=[。！？!?])|(?<=\.)\s+(?=[A-Za-z\u3400-\u9fff])",
            line,
        ):
            if not sentence:
                continue
            match = _INTERNAL_CITATION_PROSE_RE.search(sentence)
            if match is None:
                kept.append(sentence)
                continue
            prefix = sentence[: match.start()].rstrip(" \t,，:：;；-")
            if prefix and (
                _DISPLAY_NUMBER_RE.search(prefix) or _INLINE_EVIDENCE_LINK_RE.search(prefix)
            ):
                kept.append(prefix)
        cleaned = " ".join(part.strip() for part in kept if part.strip()).strip()
        if cleaned:
            output.append(cleaned)
    return "\n".join(output)


def _repair_relevance_fold(value: Any) -> str:
    return re.sub(
        r"\s+",
        "",
        str(value or "").casefold().translate(_REPAIR_RELEVANCE_TRANSLATION),
    )


def _repair_relevance_terms(value: str) -> set[str]:
    folded = _repair_relevance_fold(value)
    terms = {
        word
        for word in re.findall(r"[a-z][a-z0-9_-]{2,}", folded)
        if word not in _REPAIR_RELEVANCE_STOP_TERMS
    }
    for chunk in re.findall(r"[\u3400-\u9fff]{2,}", folded):
        for width in range(2, min(6, len(chunk)) + 1):
            for start in range(0, len(chunk) - width + 1):
                term = chunk[start : start + width]
                if term not in _REPAIR_RELEVANCE_STOP_TERMS:
                    terms.add(term)
    return terms


class SessionNotFoundError(Exception):
    """Raised when a session ID does not exist in the store."""


class PendingActionNotFoundError(Exception):
    """Raised when a ``submit_action`` references a ``pending_id`` with no
    matching ``requires_action`` event in the session's events log."""


class PendingActionConflictError(Exception):
    """Raised when ``submit_action`` is called twice for the same
    ``pending_id`` with different decisions. The first decision wins;
    callers see the previous decision in ``previous_decision``."""

    def __init__(self, pending_id: str, previous_decision: str, requested_decision: str) -> None:
        self.pending_id = pending_id
        self.previous_decision = previous_decision
        self.requested_decision = requested_decision
        super().__init__(
            f"pending {pending_id} already resolved as {previous_decision}; "
            f"refused to override with {requested_decision}"
        )


class PendingActionExpiredError(Exception):
    """Raised when ``submit_action`` references a pending that's already
    been sealed by the host (``expired`` from startup scan / timeout, or
    ``interrupted`` from a Stop press)."""

    def __init__(self, pending_id: str, reason: str) -> None:
        self.pending_id = pending_id
        self.reason = reason
        super().__init__(f"pending {pending_id} already resolved as {reason}")


class RuntimeUnavailableError(Exception):
    """Raised when ``submit_action`` arrives but no runtime is actively
    waiting on the decision (turn finished, runtime cache evicted, host
    restarted). The pending should already have been ``expired`` by the
    startup scan in that case."""


class ApprovalNotImplementedError(Exception):
    """Raised when the runtime hasn't yet wired the approval bridge
    (Slice 2 ships the API but only Slice 3 wires Claude; Codex / DeepAgents
    in Phase 2 / 3). Surfaces as 501 to the client so the front-end can
    distinguish 'not built yet' from 'rejected'."""


class PendingActionDecisionMismatchError(Exception):
    """Raised when the requested ``decision`` doesn't fit the pending's
    subject — currently ``decision="answer"`` against any subject other
    than ``clarifying_questions``. Surfaces as 400 so the client knows
    the contract was violated (vs 409 for legitimate same-pending
    racing). The reverse mismatch (approve/reject against a clarifying
    pending) is also caught here.
    """

    def __init__(self, pending_id: str, subject: str, decision: str) -> None:
        self.pending_id = pending_id
        self.subject = subject
        self.decision = decision
        super().__init__(
            f"pending {pending_id} has subject={subject!r}; decision={decision!r} is not valid"
        )


@dataclass(frozen=True)
class SubmitActionResult:
    pending_id: str
    decision: Literal["approve", "approve_with_changes", "approve_for_session", "reject", "answer"]
    accepted_at: int  # Unix epoch ms (UTC)
    idempotent: bool
    # Set when ``decision == "approve_for_session"`` — the UUID assigned to
    # the rule the user just attached. ``None`` for every other verb.
    rule_id: str | None = None


class _GlobalForwardTap:
    """Per-bus tap fanning every emit out to the orchestrator's global taps.

    Holds the orchestrator's tap list *by reference*, so taps registered
    after this bus was created still receive its events. A failing global
    tap is logged and skipped — never detached here, since the same tap
    object is shared across every session's forwarder.
    """

    def __init__(self, session_id: str, taps: list[GlobalEventTap]) -> None:
        self._session_id = session_id
        self._taps = taps

    async def emit(self, event: Event) -> None:
        for tap in list(self._taps):
            try:
                await tap.emit_session(self._session_id, event)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Global event tap failed for %s: %s", self._session_id, exc)


class _MessageIdStampSink:
    """Adds ``message_id`` to every outbound event's data dict.

    Wraps the user-facing sink (e.g. WebSocket) so clients can route events
    to the correct Message without an extra round-trip. The DatabaseEventSink
    already binds message_id at construction so it does not need this stamp;
    leaving the DB JSON free of the duplicate field keeps stored events
    clean.
    """

    def __init__(self, inner: EventSink, message_id: str) -> None:
        self._inner = inner
        self._message_id = message_id

    async def emit(self, event: Event) -> None:
        stamped = Event(
            type=event.type,
            data={**event.data, "message_id": self._message_id},
            timestamp=event.timestamp,
        )
        await self._inner.emit(stamped)


def _session_citation_quality_policy(
    session: Session,
) -> dict[str, Any] | None:
    """Read only the host-stamped, JSON-safe policy snapshot."""

    metadata = session.metadata if isinstance(session.metadata, dict) else {}
    valuz = metadata.get("valuz")
    if not isinstance(valuz, dict):
        return None
    snapshot = valuz.get("citation_quality_policy")
    if not isinstance(snapshot, dict):
        return None
    if snapshot.get("mode") not in {"required-on-evidence", "strict-domain"}:
        return None
    if not isinstance(snapshot.get("config"), dict):
        return None
    return snapshot


def _session_citation_enabled(session: Session) -> bool:
    metadata = session.metadata if isinstance(session.metadata, dict) else {}
    valuz = metadata.get("valuz")
    if not isinstance(valuz, dict):
        return True
    value = valuz.get("citation_enabled")
    return value if isinstance(value, bool) else True


def _session_citation_verification_enabled(session: Session) -> bool:
    metadata = session.metadata if isinstance(session.metadata, dict) else {}
    valuz = metadata.get("valuz")
    if not isinstance(valuz, dict):
        return False
    value = valuz.get("citation_verification_enabled")
    return value if isinstance(value, bool) else False


def _session_document_scope(session: Session) -> set[str] | None:
    """Return the host-stamped locked document scope, if present."""

    metadata = session.metadata if isinstance(session.metadata, dict) else {}
    valuz = metadata.get("valuz")
    if not isinstance(valuz, dict):
        return None
    research = valuz.get("document_research")
    if (
        not isinstance(research, dict)
        or research.get("purpose") != "document-research"
        or research.get("source_scope") != "locked"
    ):
        return None
    document_ids = research.get("document_ids")
    if not isinstance(document_ids, list):
        return set()
    return {str(item) for item in document_ids if str(item)}


class _MessageObserverSink:
    """Forwards events to ``inner`` while accumulating per-Message state.

    Captures the assistant text fragments emitted as ``assistant_message``
    events, the live ``text_delta`` stream, the ``num_turns`` reported in
    ``session_idle``, and any ``session_error`` payload. The orchestrator reads
    these accumulators when finalizing the Message row.
    """

    def __init__(
        self,
        inner: EventSink,
        *,
        message_id: str = "message",
        user_prompt: str = "",
        citation_policy_available: bool = False,
        citation_quality_policy: dict[str, Any] | None = None,
        allowed_document_ids: set[str] | None = None,
        force_citation_required: bool = False,
        citation_enabled: bool = True,
        citation_verification_enabled: bool = True,
    ) -> None:
        self._inner = inner
        self._user_prompt = user_prompt
        self._assistant_chunks: list[str] = []
        self._assistant_delta_chunks: list[str] = []
        self._pending_assistant: Event | None = None
        self._tool_names: dict[str, str] = {}
        self._evidence_registry = EvidenceRegistry(
            allowed_document_ids=allowed_document_ids,
        )
        self._citation_quality_policy = citation_quality_policy
        self._citation_enabled = citation_enabled
        self._citation_verification_enabled = citation_verification_enabled
        self._citation_guard = CitationGuard(
            self._evidence_registry,
            message_id=message_id,
            user_prompt=user_prompt,
            policy_available=citation_policy_available,
            quality_policy=citation_quality_policy,
            # ``strict-domain`` means every factual answer is audited, not
            # merely turns where the model happened to call a source tool.
            # Tying activation to Evidence Registry activity let source-free
            # finance answers bypass the guard entirely.
            force_required=(
                force_citation_required
                or (
                    isinstance(citation_quality_policy, dict)
                    and citation_quality_policy.get("mode") == "strict-domain"
                )
            ),
            enabled=citation_enabled,
            verification_enabled=citation_verification_enabled,
        )
        self._citation_repair_requested = False
        self._citation_repair_prompt: str | None = None
        self._citation_repair_attempts = 0
        self._usage_before_citation_repair: dict[str, int] | None = None
        self._citation_repair_baseline_event: Event | None = None
        self.num_turns: int = 0
        self.error_payload: dict[str, Any] | None = None
        self.usage: dict[str, int] | None = None
        self.model_usage: dict[str, Any] | None = None
        # Canonical citation sidecar from the latest final assistant event.
        # Runtimes/guards emit it once with the sealed answer; the orchestrator
        # copies it into Message.metadata during finalization.
        self.citation_bundle: dict[str, Any] | None = None
        # Last `todo_update` payload observed in this turn. None means the
        # agent did not touch the TODO list. An empty list is a meaningful
        # "all done" signal from the SDK and is preserved.
        self.last_todos: list[dict[str, Any]] | None = None
        # Captures runtime-emitted ``mode_changed{by: "runtime"}`` events
        # (codex ``thread/goal/cleared`` listener, Claude bare-``/goal``
        # poll). Used by ``run_turn`` to decide whether the final
        # ``save_session`` should honor the runtime's in-memory
        # ``session.mode`` (runtime emitted a change → keep it) or
        # reload from disk (user mutated mode mid-turn via ``POST /mode``
        # → don't clobber). ``None`` means no runtime-emitted mode
        # change observed this turn.
        self.runtime_mode_change: Literal["default", "plan", "goal"] | None = None

    async def emit(self, event: Event) -> None:
        is_top_level = event.data.get("parent_tool_use_id") is None
        if event.type == "citation_evidence":
            citation_content = event.data.get("content")
            if self._citation_enabled and isinstance(citation_content, str):
                tool_name = event.data.get("tool_name")
                self._evidence_registry.register_tool_result(
                    citation_content,
                    tool_name=str(tool_name) if tool_name else None,
                    trusted_private=True,
                )
            # This is a private bridge between the graph checkpoint and the
            # citation registry, not a user-visible tool invocation.
            return
        if event.type == "assistant_message" and is_top_level:
            # A runtime can emit multiple canonical text blocks in one turn:
            # an assistant preamble, then a tool call, then the final answer.
            # Hold only the latest top-level block until we know whether a
            # continuation follows.  This lets the Citation Guard seal the one
            # final block before either persistence or broadcast.
            if self._pending_assistant is not None:
                await self._flush_pending_assistant(final=False)
            canonical_text = str(event.data.get("text") or event.data.get("content") or "")
            streamed_text = self.partial_assistant_text or ""
            # Claude can occasionally report only the last paragraph in its
            # canonical AssistantMessage even though the immediately preceding
            # top-level text_delta stream contains the complete final answer.
            # Citation turns intentionally hide those deltas until sealing, so
            # blindly preferring that short canonical block discards the whole
            # researched answer and leaves the user with an epilogue such as
            # "以上即为……".  The stream is safe to promote only when it is a
            # materially longer superset containing the canonical block; tool
            # boundaries already clear research preambles from this buffer.
            if (
                self._citation_guard.requires_citation
                and len(streamed_text) >= max(400, len(canonical_text) * 2)
            ):
                event = Event(
                    type=event.type,
                    data={
                        **{
                            key: value
                            for key, value in event.data.items()
                            if key not in {"text", "content", "citation_bundle"}
                        },
                        "text": streamed_text,
                    },
                    timestamp=event.timestamp,
                )
                logger.warning(
                    "citation_guard preserved complete streamed answer over short "
                    "canonical block streamed=%d canonical=%d",
                    len(streamed_text),
                    len(canonical_text),
                )
            self._pending_assistant = event
            # The canonical block supersedes its already-streamed deltas.
            self._assistant_delta_chunks.clear()
            return

        if (
            self._pending_assistant is not None
            and is_top_level
            and event.type in {"text_delta", "tool_use"}
        ):
            await self._flush_pending_assistant(
                final=False,
                suppress_user_visible=(
                    event.type == "tool_use" or self._citation_guard.requires_citation
                ),
            )

        if event.type == "assistant_message":
            # Subagent text is an out-of-band flow and must not take ownership
            # of the lead's pending final block.
            self._record_assistant_message(event)
        elif event.type == "text_delta":
            text = event.data.get("text") or event.data.get("delta") or ""
            if text:
                self._assistant_delta_chunks.append(str(text))
            if is_top_level and self._citation_guard.requires_citation:
                # Source-bearing answers are provisional until the complete
                # body has passed Guard + Claim Audit.  Do not leak the first
                # draft through the live delta stream before a hidden repair
                # can replace it.  Non-citation chat keeps normal streaming.
                return
        elif event.type == "tool_use":
            tool_use_id = event.data.get("id")
            tool_name = event.data.get("name")
            if isinstance(tool_use_id, str) and isinstance(tool_name, str):
                self._tool_names[tool_use_id] = tool_name
        elif event.type == "tool_result":
            tool_use_id = event.data.get("id")
            tool_name = self._tool_names.get(tool_use_id) if isinstance(tool_use_id, str) else None
            citation_content = event.data.get("_citation_content")
            visible_content = event.data.get("content")
            compacted_content = compact_citation_tool_content(visible_content)
            if self._citation_enabled:
                private_projection = (
                    citation_content
                    if isinstance(citation_content, str)
                    else private_citation_tool_content(visible_content)
                    if compacted_content is not None
                    else None
                )
                self._evidence_registry.register_tool_projection(
                    compacted_content if compacted_content is not None else visible_content,
                    private_projection,
                    tool_name=tool_name,
                    trusted_private=(
                        private_projection is not None or compacted_content is not None
                    ),
                )
            if "_citation_content" in event.data or compacted_content is not None:
                # The full evidence payload is turn-private: the Registry has
                # consumed it, so persist/broadcast only the compact model
                # view (or the runtime's existing placeholder).
                event = Event(
                    type=event.type,
                    data={
                        key: (compacted_content if key == "content" else value)
                        for key, value in event.data.items()
                        if key != "_citation_content"
                    },
                    timestamp=event.timestamp,
                )
        elif event.type == "session_idle":
            raw = event.data.get("num_turns")
            if isinstance(raw, int) and raw > 0:
                if self._citation_repair_attempts:
                    self.num_turns += raw
                else:
                    self.num_turns = raw
            stop_reason = event.data.get("stop_reason")
            allow_repair = not (
                isinstance(stop_reason, dict)
                and stop_reason.get("type") in {"error", "user_interrupt", "budget_exhausted"}
            )
            repair_aborted = self._citation_repair_attempts > 0 and not allow_repair
            if repair_aborted:
                reason = (
                    str(stop_reason.get("type") or "error")
                    if isinstance(stop_reason, dict)
                    else "error"
                )
                published_baseline = await self.publish_citation_repair_baseline_on_abort(
                    reason=reason
                )
                if not published_baseline:
                    await self.ensure_partial_assistant_message(allow_repair=False)
            else:
                await self.ensure_partial_assistant_message(allow_repair=allow_repair)
            if self._citation_repair_requested:
                # Keep the turn running while the orchestrator sends one
                # hidden repair instruction to the same runtime.  The failed
                # candidate and this interim idle frame are neither persisted
                # nor broadcast.
                return
            await self._inner.emit(event)
            return
        elif event.type == "session_error":
            self.error_payload = {
                "category": "execution_error",
                "message": str(event.data.get("message", "")),
            }
        elif event.type == "usage_update":
            current_usage = {
                "input_tokens": int(event.data.get("input_tokens") or 0),
                "output_tokens": int(event.data.get("output_tokens") or 0),
                "cache_read_tokens": int(event.data.get("cache_read_tokens") or 0),
                "cache_write_tokens": int(event.data.get("cache_write_tokens") or 0),
            }
            if self._usage_before_citation_repair is None:
                self.usage = current_usage
            else:
                self.usage = {
                    key: self._usage_before_citation_repair[key] + current_usage[key]
                    for key in current_usage
                }
            raw_model_usage = event.data.get("model_usage")
            self.model_usage = dict(raw_model_usage) if isinstance(raw_model_usage, dict) else None
        elif event.type == "todo_update":
            raw_todos = event.data.get("todos")
            if isinstance(raw_todos, list):
                self.last_todos = [dict(t) for t in raw_todos if isinstance(t, dict)]
        elif event.type == "mode_changed":
            if event.data.get("by") == "runtime":
                raw_mode = event.data.get("mode")
                if raw_mode in ("default", "plan", "goal"):
                    self.runtime_mode_change = raw_mode
        await self._inner.emit(event)

    def _record_assistant_message(self, event: Event) -> None:
        text = event.data.get("text") or event.data.get("content") or ""
        if text:
            self._assistant_chunks.append(str(text))
            self._assistant_delta_chunks.clear()
        citation_bundle = event.data.get("citation_bundle")
        if isinstance(citation_bundle, dict):
            # Copy the shallow top-level container so later runtime mutations
            # cannot replace the canonical sidecar after the event was emitted.
            self.citation_bundle = dict(citation_bundle)

    @property
    def citation_repair_requested(self) -> bool:
        return self._citation_repair_requested

    @property
    def citation_repair_prompt(self) -> str:
        return self._citation_repair_prompt or _CITATION_REPAIR_PROMPT

    def begin_citation_repair(self) -> None:
        """Consume the one retry request and preserve first-run usage."""

        if not self._citation_repair_requested:
            return
        self._citation_repair_requested = False
        self._citation_repair_attempts += 1
        self._usage_before_citation_repair = dict(self.usage) if self.usage is not None else None
        # A runtime may emit only text_delta frames and no final
        # assistant_message.  The withheld first draft must not be prefixed to
        # the repaired attempt.
        self._assistant_delta_chunks.clear()
        self._pending_assistant = None

    async def publish_citation_repair_baseline_on_abort(self, *, reason: str) -> bool:
        """Discard an interrupted repair and publish the sealed first draft.

        The repair prompt and its partial output are turn-private protocol
        state.  If the runtime is interrupted, errors, or exhausts its budget,
        persisting that partial can expose evidence handles, validation codes,
        tool failures, and repair instructions.  The first draft has already
        passed the deterministic guard, so it is the only safe fallback.
        """

        baseline = self._citation_repair_baseline_event
        if baseline is None:
            return False
        self._pending_assistant = None
        self._assistant_delta_chunks.clear()
        self._citation_repair_requested = False
        published = copy.deepcopy(baseline)
        self._mark_repair_outcome(
            published,
            outcome="aborted",
            abort_reason=reason,
        )
        self._citation_repair_baseline_event = None
        self._record_assistant_message(published)
        await self._inner.emit(published)
        logger.warning(
            "citation_guard published sealed baseline after repair abort reason=%s",
            reason,
        )
        return True

    async def ensure_partial_assistant_message(
        self,
        *,
        allow_repair: bool = True,
    ) -> bool:
        if self._pending_assistant is not None:
            return await self._flush_pending_assistant(
                final=True,
                allow_repair=allow_repair,
            )
        text = self.partial_assistant_text
        if not text:
            return False
        event = self._build_final_assistant_event(
            text,
            allow_repair=allow_repair,
        )
        if event is None:
            return False
        self._record_assistant_message(event)
        await self._inner.emit(event)
        return True

    async def _flush_pending_assistant(
        self,
        *,
        final: bool,
        allow_repair: bool = True,
        suppress_user_visible: bool = False,
    ) -> bool:
        pending = self._pending_assistant
        if pending is None:
            return False
        self._pending_assistant = None
        raw_text = pending.data.get("text") or pending.data.get("content") or ""
        if suppress_user_visible:
            # A top-level assistant block immediately followed by a tool call
            # is the model's research preamble, not its answer. Persisting it
            # used to leak remembered values, fake evidence links, raw ids, and
            # "now I will fetch" narration ahead of the later guarded answer.
            # Tool events already represent this progress in the UI.
            self._assistant_delta_chunks.clear()
            return False
        if _INTERNAL_HANDOFF_RE.search(str(raw_text)):
            # DeepAgents' context-compaction middleware may surface its
            # machine-to-machine handoff as a top-level assistant block.  It
            # is runtime state, not an answer, and must never be persisted or
            # broadcast to the user.
            self._assistant_delta_chunks.clear()
            return False
        data = {
            key: value
            for key, value in pending.data.items()
            if key not in {"text", "content", "citation_bundle"}
        }
        data["text"] = str(raw_text)
        if final:
            event = self._build_final_assistant_event(
                str(raw_text),
                base_data=data,
                timestamp=pending.timestamp,
                allow_repair=allow_repair,
            )
            if event is None:
                return False
        else:
            event = Event(type="assistant_message", data=data, timestamp=pending.timestamp)
        self._record_assistant_message(event)
        await self._inner.emit(event)
        return True

    def _build_final_assistant_event(
        self,
        raw_text: str,
        *,
        base_data: dict[str, Any] | None = None,
        timestamp: int | None = None,
        allow_repair: bool,
    ) -> Event | None:
        if self._citation_repair_attempts and self._citation_repair_baseline_event is not None:
            baseline = self._citation_repair_baseline_event
            baseline_text = baseline.data.get("text")
            baseline_bundle = baseline.data.get("citation_bundle")
            output_contract = parse_output_contract(self._user_prompt)
            quality = (
                baseline_bundle.get("quality")
                if isinstance(baseline_bundle, dict)
                else None
            )
            raw_claims = quality.get("claims") if isinstance(quality, dict) else None
            baseline_for_contract = baseline_text if isinstance(baseline_text, str) else ""
            required_fields_by_claim = {
                str(claim.get("claimId")): output_contract.required_fields_for_claim(
                    baseline_for_contract[
                        int(claim["location"]["sourceStart"]):int(
                            claim["location"]["sourceEnd"]
                        )
                    ]
                )
                for claim in (raw_claims if isinstance(raw_claims, list) else [])
                if isinstance(claim, dict)
                and isinstance(claim.get("claimId"), str)
                and isinstance(claim.get("location"), dict)
                and isinstance(claim["location"].get("sourceStart"), int)
                and isinstance(claim["location"].get("sourceEnd"), int)
            }
            patch_result = apply_citation_claim_patch(
                baseline_text=baseline_text if isinstance(baseline_text, str) else "",
                baseline_bundle=(
                    baseline_bundle if isinstance(baseline_bundle, dict) else None
                ),
                response_text=raw_text,
                allowed_claim_ids=repairable_claim_ids(
                    baseline_bundle if isinstance(baseline_bundle, dict) else None,
                    repairable_issue_codes=_ACTIONABLE_REPAIR_ISSUE_CODES,
                ),
                allowed_evidence_handles={
                    record.handle for record in self._evidence_registry.values()
                },
                required_fields_by_claim=required_fields_by_claim,
            )
            if not patch_result.accepted or patch_result.text is None:
                rejected = copy.deepcopy(baseline)
                self._mark_repair_outcome(
                    rejected,
                    outcome=f"rejected-protocol-{patch_result.code or 'invalid'}",
                )
                self._citation_repair_baseline_event = None
                logger.warning(
                    "citation_guard rejected claim patch reason=%s and published baseline",
                    patch_result.code,
                )
                return rejected
            raw_text = patch_result.text
        raw_text = _strip_leading_assistant_progress(raw_text)
        if not raw_text.strip():
            return None
        raw_text = _attach_standalone_citation_lines(raw_text)
        raw_text = _strip_strict_scope_leadin(raw_text, self._user_prompt)
        raw_text = _strip_unrequested_source_excerpt(raw_text, self._user_prompt)
        raw_text = _strip_unrequested_derived_restatement(raw_text, self._user_prompt)
        raw_text = _strip_unrequested_period_leadin(raw_text, self._user_prompt)
        raw_text = _strip_unrequested_cross_period_recap(raw_text, self._user_prompt)
        raw_text = _strip_unrequested_retrieval_internals(raw_text, self._user_prompt)
        raw_text = _strip_empty_markdown_labels(raw_text)
        raw_text = _strip_empty_markdown_tables(raw_text)
        raw_text = raw_text.strip()
        result = self._citation_guard.finalize(
            raw_text,
            repair_attempts=self._citation_repair_attempts,
        )
        data = dict(base_data or {})
        data["text"] = result.text
        if result.bundle is not None:
            data["citation_bundle"] = result.bundle
            integrity = result.bundle.get("integrity") or {}
            logger.info(
                "citation_guard sealed message status=%s citations=%d unknown=%d",
                integrity.get("status"),
                len(result.bundle.get("citations") or []),
                len(integrity.get("unknownCitationIds") or []),
            )
            quality = result.bundle.get("quality")
            if isinstance(quality, dict):
                metrics = quality.get("metrics")
                metrics = metrics if isinstance(metrics, dict) else {}
                logger.info(
                    "citation_quality policy=%s revision=%s status=%s "
                    "citations=%s unsourced=%s unverified=%s",
                    quality.get("policyId"),
                    quality.get("policyRevision"),
                    quality.get("status"),
                    metrics.get("citationCount", 0),
                    metrics.get("unsourcedClaimCount", 0),
                    metrics.get("unverifiedClaimCount", 0),
                )

        event = Event(
            type="assistant_message",
            data=data,
            **({"timestamp": timestamp} if timestamp is not None else {}),
        )
        needs_repair = self._citation_publication_needs_repair(result.bundle)
        if allow_repair and needs_repair and self._citation_repair_attempts == 0:
            repair_prompt = self._build_citation_repair_prompt(
                result.bundle,
                result.text,
            )
            skip_reason = self._citation_repair_skip_reason(
                result.bundle,
                result.text,
                repair_prompt=repair_prompt,
            )
            if skip_reason is not None:
                self._mark_repair_outcome(
                    event,
                    outcome="skipped",
                    skip_reason=skip_reason,
                )
                logger.warning(
                    "citation_guard skipped automatic repair and retained draft reason=%s",
                    skip_reason,
                )
                return event
            self._citation_repair_baseline_event = copy.deepcopy(event)
            self._citation_repair_requested = True
            self._citation_repair_prompt = repair_prompt
            logger.warning("citation_guard withheld draft and requested one repair pass")
            return None
        if self._citation_repair_attempts:
            baseline = self._citation_repair_baseline_event
            if baseline is not None and not self._repair_improves(
                baseline,
                event,
                strict_output_scope=bool(_STRICT_OUTPUT_SCOPE_RE.search(self._user_prompt)),
            ):
                logger.warning(
                    "citation_guard repair metrics before=%s after=%s",
                    self._repair_metrics(baseline),
                    self._repair_metrics(event),
                )
                rejected = copy.deepcopy(baseline)
                self._mark_repair_outcome(rejected, outcome="rejected-no-improvement")
                self._citation_repair_baseline_event = None
                logger.warning(
                    "citation_guard rejected automatic repair because quality did not improve"
                )
                return rejected
            self._mark_repair_outcome(event, outcome="accepted")
            self._citation_repair_baseline_event = None
            if needs_repair:
                logger.warning("citation_guard published improved but still degraded repair")
        return event

    def _build_citation_repair_prompt(
        self,
        bundle: dict[str, Any] | None,
        draft_text: str,
    ) -> str:
        quality = bundle.get("quality") if isinstance(bundle, dict) else None
        quality = quality if isinstance(quality, dict) else {}
        raw_claims = quality.get("claims")
        raw_claims = raw_claims if isinstance(raw_claims, list) else []
        raw_issues = quality.get("issues")
        raw_issues = raw_issues if isinstance(raw_issues, list) else []
        policy_mode, semantics = self._citation_policy_context()
        extracted = extract_claims(
            draft_text,
            mode=policy_mode,
            semantics=semantics,
        )
        extracted_by_id = {claim.claim_id: claim for claim in extracted}
        extracted_by_exact = {claim.exact.strip(): claim for claim in extracted}
        claim_issues: list[dict[str, Any]] = []
        candidate_evidence: dict[str, dict[str, Any]] = {}
        registry_records = list(self._evidence_registry.values())[:200]
        records_by_handle = {record.handle: record for record in registry_records}
        for claim in raw_claims:
            if not isinstance(claim, dict) or claim.get("citationRequired") is not True:
                continue
            issue_codes = claim.get("issueCodes")
            issue_codes = (
                [
                    value
                    for value in issue_codes
                    if isinstance(value, str) and value in _ACTIONABLE_REPAIR_ISSUE_CODES
                ]
                if isinstance(issue_codes, list)
                else []
            )
            if not issue_codes:
                continue
            exact = claim.get("exact")
            exact_text = str(exact)[:500] if exact is not None else ""
            location = claim.get("location")
            location = location if isinstance(location, dict) else {}
            source_start = location.get("sourceStart")
            source_end = location.get("sourceEnd")
            source_text = (
                draft_text[source_start:source_end]
                if isinstance(source_start, int)
                and isinstance(source_end, int)
                and 0 <= source_start < source_end <= len(draft_text)
                else exact_text
            )
            extracted_claim = extracted_by_id.get(str(claim.get("claimId") or ""))
            if extracted_claim is None:
                extracted_claim = extracted_by_exact.get(exact_text.strip())
            candidate_handles: list[str] = []
            if extracted_claim is not None:
                resolution = resolve_claim_evidence(
                    extracted_claim,
                    registry_records,
                    semantics=semantics,
                )
                if resolution.binding_action == "auto-rebind":
                    candidate_handles = list(resolution.selected_handles)
                elif _ACTIONABLE_REPAIR_ISSUE_CODES.intersection(issue_codes):
                    contradicted_handles = [
                        handle
                        for handle in resolution.candidate_handles
                        if resolution.support_by_handle.get(handle) == "contradicted"
                    ]
                    # A local value patch is safe only when the Resolver found
                    # one concrete conflicting data point. Multiple possible
                    # records remain ambiguous and must not trigger a model.
                    if len(contradicted_handles) == 1:
                        candidate_handles = contradicted_handles
                for handle in candidate_handles:
                    record = records_by_handle.get(handle)
                    if record is not None:
                        candidate_evidence.setdefault(
                            handle,
                            self._repair_evidence_summary(record),
                        )
            if not candidate_handles:
                continue
            claim_issues.append(
                {
                    "claimId": claim.get("claimId"),
                    "exact": exact_text,
                    "sourceText": source_text[:1_000],
                    "locationKind": location.get("kind"),
                    "issueCodes": issue_codes[:20],
                    "citationIds": [
                        value for value in claim.get("citationIds", []) if isinstance(value, str)
                    ][:20],
                    "candidateHandles": candidate_handles,
                }
            )
            if len(claim_issues) >= _MAX_CITATION_REPAIR_CLAIMS:
                break
        context = {
            "patchVersion": CITATION_CLAIM_PATCH_VERSION,
            "originalRequest": self._user_prompt,
            "outputContract": parse_output_contract(self._user_prompt).to_dict(),
            "failedDraft": draft_text,
            "claimIssues": claim_issues,
            # Evidence is catalogued once and referenced by handle above.  The
            # old per-claim embedding repeated the same long chunk dozens of
            # times and could make a tiny answer repair larger than the whole
            # original research turn.
            "candidateEvidence": list(candidate_evidence.values()),
            "generalIssues": [
                str(entry.get("code"))
                for entry in raw_issues[:50]
                if isinstance(entry, dict)
                and isinstance(entry.get("code"), str)
                and entry.get("code") in _ACTIONABLE_REPAIR_ISSUE_CODES
            ],
        }
        return (
            _CITATION_REPAIR_PROMPT.rstrip()
            + "\n\nRestricted repair context (JSON):\n"
            + json.dumps(context, ensure_ascii=False, separators=(",", ":"))
        )

    def _request_relevant_repair_evidence_records(self) -> list[Any]:
        """Rank evidence that names the metrics in the original request."""

        terms = _repair_relevance_terms(self._user_prompt)
        if not terms:
            return []
        ranked: list[tuple[int, int, Any]] = []
        for index, record in enumerate(self._evidence_registry.values()):
            evidence = record.evidence
            body = _repair_relevance_fold(
                " ".join(
                    str(evidence.get(key) or "")
                    for key in (
                        "quote",
                        "snippet",
                        "field",
                        "metric",
                        "entityName",
                    )
                )
            )
            title = _repair_relevance_fold(record.source.get("title") or "")
            score = sum(
                len(term) * len(term) * (3 if term in body else 1)
                for term in terms
                if term in body or term in title
            )
            if score:
                ranked.append((-score, index, record))
        ranked.sort(key=lambda item: (item[0], item[1]))
        return [record for _score, _index, record in ranked[:12]]

    def _diverse_repair_evidence_records(self) -> list[Any]:
        """Round-robin the repair catalogue across registered sources."""

        groups: dict[str, list[Any]] = {}
        for record in self._evidence_registry.values():
            source = record.source
            source_key = ""
            for key in ("documentId", "sourceId", "canonicalUrl"):
                value = source.get(key)
                if isinstance(value, str) and value.strip():
                    source_key = value.strip()
                    break
            identity = (
                f"{source.get('providerId') or ''}\0{source_key}"
                if source_key
                else f"handle\0{record.handle}"
            )
            groups.setdefault(identity, []).append(record)
        selected: list[Any] = []
        depth = 0
        while len(selected) < _MAX_CITATION_REPAIR_EVIDENCE:
            added = False
            for records in groups.values():
                if depth >= len(records):
                    continue
                selected.append(records[depth])
                added = True
                if len(selected) >= _MAX_CITATION_REPAIR_EVIDENCE:
                    break
            if not added:
                break
            depth += 1
        return selected

    def _citation_policy_context(self) -> tuple[str, dict[str, Any] | None]:
        policy = self._citation_quality_policy
        if not isinstance(policy, dict):
            return "required-on-evidence", None
        config = policy.get("config")
        config = config if isinstance(config, dict) else {}
        semantics = config.get("semantics")
        return (
            str(policy.get("mode") or "required-on-evidence"),
            semantics if isinstance(semantics, dict) else None,
        )

    @staticmethod
    def _repair_evidence_summary(record: Any) -> dict[str, Any]:
        evidence = record.evidence
        source = record.source
        summary: dict[str, Any] = {
            "evidenceHandle": record.handle,
            "sourceTitle": str(source.get("title") or "")[:240],
            "providerId": str(source.get("providerId") or "")[:120],
            "sourceId": str(source.get("sourceId") or "")[:240],
            "documentId": str(source.get("documentId") or "")[:240],
            "kind": evidence.get("kind"),
        }
        if record.locator is not None:
            summary["locator"] = dict(record.locator)
        if evidence.get("kind") == "structured-data":
            summary.update(
                {
                    "field": evidence.get("field"),
                    "value": evidence.get("value"),
                    "unit": evidence.get("unit"),
                    "period": evidence.get("period"),
                    "asOf": evidence.get("asOf"),
                }
            )
        elif evidence.get("kind") == "text":
            summary["quote"] = str(evidence.get("quote") or "")[:800]
        elif evidence.get("kind") == "calculation":
            raw_inputs = evidence.get("inputs")
            raw_inputs = raw_inputs if isinstance(raw_inputs, list) else []
            summary.update(
                {
                    "expression": str(evidence.get("expression") or "")[:240],
                    "result": evidence.get("result"),
                    "unit": evidence.get("unit"),
                    "inputs": [
                        {
                            "name": str(item.get("name") or "")[:120],
                            "evidenceHandle": item.get("citationId"),
                            "value": item.get("value"),
                            "unit": item.get("unit"),
                        }
                        for item in raw_inputs[:16]
                        if isinstance(item, dict) and isinstance(item.get("citationId"), str)
                    ],
                }
            )
        return summary

    def _citation_repair_skip_reason(
        self,
        bundle: dict[str, Any] | None,
        draft_text: str,
        *,
        repair_prompt: str | None = None,
    ) -> str | None:
        if len(draft_text) > _MAX_CITATION_REPAIR_DRAFT_CHARS:
            return "draft-size-budget"
        if repair_prompt is not None and "Restricted repair context (JSON):\n" in repair_prompt:
            raw_context = repair_prompt.rsplit("Restricted repair context (JSON):\n", 1)[1]
            try:
                repair_context = json.loads(raw_context)
            except (TypeError, ValueError):
                repair_context = {}
            if not isinstance(repair_context, dict):
                repair_context = {}
            if not repair_context.get("claimIssues") or not repair_context.get(
                "candidateEvidence"
            ):
                return "no-actionable-resolution"
        citations = bundle.get("citations") if isinstance(bundle, dict) else None
        if (
            not len(self._evidence_registry)
            and not citations
            and _NO_RETRIEVAL_REQUEST_RE.search(self._user_prompt)
        ):
            return "user-requested-no-retrieval"
        quality = bundle.get("quality") if isinstance(bundle, dict) else None
        claims = quality.get("claims") if isinstance(quality, dict) else None
        problematic = sum(
            1
            for claim in claims or []
            if isinstance(claim, dict)
            and claim.get("citationRequired") is True
            and bool(
                _REPAIRABLE_CLAIM_ISSUE_CODES.intersection(
                    value for value in claim.get("issueCodes", []) if isinstance(value, str)
                )
            )
        )
        metrics = quality.get("metrics") if isinstance(quality, dict) else None
        metrics = metrics if isinstance(metrics, dict) else {}
        claim_detected = metrics.get("claimDetectedCount")
        claims_complete = (
            isinstance(claims, list)
            and isinstance(claim_detected, int)
            and claim_detected == len(claims)
            and metrics.get("claimAuditTruncated") is not True
        )
        if not claims_complete:
            # Legacy or truncated bundles may omit claim rows, so their
            # aggregate counters remain the conservative fallback. Modern
            # complete bundles use only explicitly repairable issue codes;
            # advisory translation-review rows must not consume the twelve
            # claim repair budget or trigger a pointless hidden model pass.
            problematic = max(
                problematic,
                int(metrics.get("unsourcedClaimCount") or 0)
                + int(metrics.get("unverifiedClaimCount") or 0),
            )
        # The patch protocol exposes at most twelve explicit claim issues.
        # Never run a hidden repair that provably cannot see and patch the full
        # failed set; retain the sealed answer instead of spending another
        # model pass on a partial, potentially destructive rewrite.
        if problematic > _MAX_CITATION_REPAIR_PROBLEM_CLAIMS:
            return "claim-count-budget"
        # Repair runs in an isolated runtime thread, so cumulative tokens from
        # the research/planning turn are not its marginal input.  Bound the
        # actual compact repair payload instead of skipping precisely the
        # multi-document answers that most need repair.
        if repair_prompt is not None and len(repair_prompt) > _MAX_CITATION_REPAIR_CONTEXT_CHARS:
            return "repair-context-budget"
        return None

    @staticmethod
    def _repair_metrics(event: Event) -> dict[str, int]:
        bundle = event.data.get("citation_bundle")
        bundle = bundle if isinstance(bundle, dict) else {}
        integrity = bundle.get("integrity")
        integrity = integrity if isinstance(integrity, dict) else {}
        quality = bundle.get("quality")
        quality = quality if isinstance(quality, dict) else {}
        metrics = quality.get("metrics")
        metrics = metrics if isinstance(metrics, dict) else {}
        claims = quality.get("claims")
        claims = claims if isinstance(claims, list) else []
        text = event.data.get("text")
        text = text if isinstance(text, str) else ""
        visible_text = re.sub(
            r"\[[^\]]*\]\((?:evidence|citation)://[^)]+\)",
            "",
            text,
            flags=re.IGNORECASE,
        )
        return {
            "problem": (
                int(metrics.get("unsourcedClaimCount") or 0)
                + int(metrics.get("unverifiedClaimCount") or 0)
                + len(integrity.get("unknownCitationIds") or [])
                + len(integrity.get("missingLocatorCitationIds") or [])
            ),
            "unknown": len(integrity.get("unknownCitationIds") or []),
            "mismatch": int(metrics.get("claimSemanticMismatchCount") or 0),
            "supported": sum(
                1
                for claim in claims
                if isinstance(claim, dict)
                and claim.get("citationRequired") is True
                and claim.get("status") in {"passed", "auto-bound", "repaired"}
                and not claim.get("issueCodes")
            ),
            "required": sum(
                1
                for claim in claims
                if isinstance(claim, dict) and claim.get("citationRequired") is True
            ),
            "values": len(
                re.findall(
                    r"(?<![A-Za-z0-9_])[-+]?\d[\d,]*(?:\.\d+)?(?:%|％)?",
                    visible_text,
                )
            ),
            "material_values": len(
                re.findall(
                    r"(?<![A-Za-z0-9_])[-+]?(?:"
                    r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|"
                    r"\d+\.\d+|"
                    r"\d+(?:%|％|亿元|万元|百万元|元|USD|CNY)"
                    r")",
                    visible_text,
                    flags=re.IGNORECASE,
                )
            ),
        }

    @classmethod
    def _repair_improves(
        cls,
        baseline: Event,
        candidate: Event,
        *,
        strict_output_scope: bool = False,
    ) -> bool:
        before = cls._repair_metrics(baseline)
        after = cls._repair_metrics(candidate)
        # Small answers must retain every requested claim.  Large drafts often
        # contain model-added recaps, forecasts, or duplicate tables; requiring
        # their entire claim count made an otherwise valid scope-correct repair
        # impossible.  For those drafts, retain every already-supported claim
        # and at least half of factual coverage while allowing unsupported
        # expansion to be removed.
        minimum_required = before["required"]
        # Once a draft contains more than a handful of atomic claims, raw
        # claim count is no longer a reliable proxy for request coverage: one
        # requested fact may be repeated in a heading, quote, explanation and
        # recap.  Permit a repair to remove that unsupported duplication while
        # retaining every already-supported claim and at least half of the
        # factual coverage.  Small answers remain lossless.
        if before["required"] > 6:
            minimum_required = max(
                before["supported"],
                (before["required"] + 1) // 2,
            )
        if strict_output_scope:
            # A strict "only these fields" request often produces a bloated
            # first draft with many unrequested factual fragments. Counting
            # half of that accidental expansion as mandatory caused a much
            # better, scope-correct repair to be rejected. Preserve every
            # already-supported claim, but let repair remove unsupported
            # explanations, recaps and proxy metrics.
            minimum_required = before["supported"]
        return (
            after["problem"] < before["problem"]
            and after["unknown"] <= before["unknown"]
            and after["supported"] >= before["supported"]
            and after["required"] >= minimum_required
            # A repair must not erase already-supported numeric content and
            # turn it into a source-coverage refusal.  When the draft had no
            # supported claim at all, however, replacing an invented number
            # with the qualitative fact actually present in the evidence is
            # a genuine improvement.  The rejected draft is still published
            # with neutral advisory citations.
            and (
                (before["supported"] == 0 and after["supported"] > 0)
                or before["material_values"] == 0
                or after["material_values"] > 0
            )
            and (
                (before["supported"] == 0 and after["supported"] > 0)
                or before["required"] > 6
                or after["values"] >= before["values"]
            )
        )

    @staticmethod
    def _mark_repair_outcome(
        event: Event,
        *,
        outcome: str,
        skip_reason: str | None = None,
        abort_reason: str | None = None,
    ) -> None:
        bundle = event.data.get("citation_bundle")
        if not isinstance(bundle, dict):
            return
        integrity = bundle.get("integrity")
        if not isinstance(integrity, dict):
            integrity = {}
            bundle["integrity"] = integrity
        integrity["repairOutcome"] = outcome
        if skip_reason is not None:
            integrity["repairSkippedReason"] = skip_reason
        if abort_reason is not None:
            integrity["repairAbortReason"] = abort_reason
        if outcome.startswith("rejected") or outcome == "aborted":
            integrity["repairAttempts"] = 1
            integrity["status"] = "degraded"

    def _citation_publication_needs_repair(
        self,
        bundle: dict[str, Any] | None,
    ) -> bool:
        # Citation-only mode is intentionally deterministic and inexpensive.
        # The guard may canonicalize trusted handles and discard unknown ones,
        # but an LLM repair pass belongs to the opt-in verification workflow.
        # Otherwise merely enabling citation display can double latency and
        # token usage even though the user explicitly disabled verification.
        if not self._citation_verification_enabled:
            return False
        # A generated bundle means this turn crossed the citation boundary.
        # Missing retrieval and unresolved support are not actionable local
        # patches: a tool-isolated repair cannot create trusted Evidence and
        # must not spend a second model pass merely to restate uncertainty.
        if not isinstance(bundle, dict):
            return False
        quality = bundle.get("quality")
        quality = quality if isinstance(quality, dict) else {}
        if not len(self._evidence_registry):
            return False
        claims = quality.get("claims")
        claims = claims if isinstance(claims, list) else []
        for claim in claims:
            if not isinstance(claim, dict) or claim.get("citationRequired") is not True:
                continue
            issue_codes = claim.get("issueCodes")
            if isinstance(issue_codes, list):
                claim_codes = {value for value in issue_codes if isinstance(value, str)}
                if _ACTIONABLE_REPAIR_ISSUE_CODES.intersection(claim_codes):
                    return True
        return False

    @property
    def assistant_text(self) -> str | None:
        partial = self.partial_assistant_text
        if self._assistant_chunks:
            chunks = list(self._assistant_chunks)
            if partial:
                chunks.append(partial)
            return "\n".join(chunks)
        return partial

    @property
    def partial_assistant_text(self) -> str | None:
        if not self._assistant_delta_chunks:
            return None
        text = "".join(self._assistant_delta_chunks)
        return text or None


class SessionOrchestrator:
    """Manages Runtime lifecycle for sessions.

    Responsibilities:
    1. Bind the runtime to the session's embedded AgentConfig snapshot
    2. Runtime caching per session (config changes take effect on new sessions)
    3. Active runtime tracking (interrupt support)
    4. Per-run Message lifecycle (one row per call to run_turn)

    Sessions sharing a cwd may run concurrently; the user is responsible for
    any workspace contention that arises (e.g. two sessions editing the same
    file).
    """

    # Warm-runtime eviction defaults. Each cached runtime holds a live CLI
    # subprocess (claude / codex) for the life of the cache entry, so an
    # unbounded ``_runtimes`` leaks one OS process per session touched —
    # they only die when the host exits (the SDKs' atexit reaper). These two
    # knobs bound that: a hard LRU ceiling on concurrent warm runtimes and an
    # idle TTL after which an untouched runtime is closed. ``<= 0`` disables
    # the corresponding policy. Overridable per-instance (composition root
    # reads env in ``app.dependencies``); see docs/design or the eviction
    # helpers below.
    DEFAULT_MAX_WARM_RUNTIMES: int = 6
    DEFAULT_RUNTIME_IDLE_TTL_S: float = 300.0  # 5 min
    DEFAULT_SWEEP_INTERVAL_S: float = 60.0  # 1 min
    # Extended idle TTL for runtimes reporting live background tasks
    # (``run_in_background`` processes die with the CLI subprocess, so normal
    # TTL eviction would kill user work mid-task). An EXTENSION rather than an
    # exemption: a crashed CLI can leave the busy signal stuck, and this is
    # the backstop against pinning a runtime forever. ``<= 0`` = full
    # exemption (busy runtimes never TTL-evicted).
    DEFAULT_BG_BUSY_RUNTIME_TTL_S: float = 3600.0  # 1 h

    def __init__(
        self,
        store: StorePort,
        *,
        max_warm_runtimes: int | None = None,
        runtime_idle_ttl_s: float | None = None,
        sweep_interval_s: float | None = None,
        bg_busy_runtime_ttl_s: float | None = None,
    ) -> None:
        self._store = store
        self._runtimes: dict[str, RuntimePort] = {}
        # session_id -> monotonic timestamp of the last turn START/END on that
        # cached runtime. Drives idle-TTL + LRU eviction. Mirrors the lifetime
        # of ``_runtimes`` exactly (added on create, dropped on evict/cleanup).
        self._runtime_last_used: dict[str, float] = {}
        self._max_warm_runtimes = (
            self.DEFAULT_MAX_WARM_RUNTIMES if max_warm_runtimes is None else max_warm_runtimes
        )
        self._runtime_idle_ttl_s = (
            self.DEFAULT_RUNTIME_IDLE_TTL_S if runtime_idle_ttl_s is None else runtime_idle_ttl_s
        )
        self._sweep_interval_s = (
            self.DEFAULT_SWEEP_INTERVAL_S if sweep_interval_s is None else sweep_interval_s
        )
        self._bg_busy_runtime_ttl_s = (
            self.DEFAULT_BG_BUSY_RUNTIME_TTL_S
            if bg_busy_runtime_ttl_s is None
            else bg_busy_runtime_ttl_s
        )
        # Background idle-sweeper task. Started by ``start()`` (composition
        # root, has a running loop), cancelled by ``shutdown()``. ``None`` when
        # not running — the lazy sweep in ``_ensure_runtime`` still enforces
        # both policies on every turn, so eviction is correct even if the timer
        # was never started (e.g. unit tests driving ``_ensure_runtime``).
        self._sweeper_task: asyncio.Task[None] | None = None
        self._closing = False
        self._active: dict[str, RuntimePort] = {}
        self._active_message: dict[str, Message] = {}
        # Per-session outbound bus. Lifecycle is independent of any
        # particular WebSocket: the runtime always emits to the bus, and
        # the bus forwards to whichever client sink (if any) is currently
        # attached. Drops on disconnect, replays on reattach.
        self._buses: dict[str, SessionEventBus] = {}
        # Session-scoped approval rules (``approve_for_session`` verb).
        # Kernel-owned so the event-flow contract stays uniform across
        # runtimes — see ``docs/design/approve-for-session.md`` §4.1.
        # Cleared on ``cleanup(session_id)``; not persisted to DB in v2.
        self._session_approval_cache = SessionApprovalCache()
        # Process-wide event taps: each receives ``(session_id, event)``
        # for every event emitted on ANY session bus. The list object is
        # shared by reference with the per-bus forwarders created in
        # ``_get_or_create_bus``, so registration is effective for buses
        # created both before and after the tap was added.
        self._global_taps: list[GlobalEventTap] = []
        # Optional host seam used by the in-process Valuz composition.  The
        # kernel remains standalone-capable: remote/bare kernels leave it
        # unset and run the repair with the session snapshot they already own.
        self._citation_repair_refresh_hook: CitationRepairRefreshHook | None = None

    @property
    def active_sessions(self) -> set[str]:
        return set(self._active)

    def has_cached_runtime(self, session_id: str) -> bool:
        return session_id in self._runtimes

    def set_citation_repair_refresh_hook(
        self,
        hook: CitationRepairRefreshHook | None,
    ) -> None:
        """Install the host credential-refresh seam for hidden repair runs."""

        self._citation_repair_refresh_hook = hook

    def _get_or_create_bus(self, session_id: str) -> SessionEventBus:
        bus = self._buses.get(session_id)
        if bus is None:
            bus = SessionEventBus(
                taps=[_GlobalForwardTap(session_id, self._global_taps)],
                session_id=session_id,
            )
            self._buses[session_id] = bus
        return bus

    async def attach_session_tap(
        self,
        user_id: str,
        session_id: str,
        sink: EventSink,
        *,
        replay: bool = False,
        live_partial: bool = False,
    ) -> None:
        """Register a passive multi-subscriber tap on a session's live stream.

        Unlike :meth:`attach_session_sink` (the single client slot used by
        the WS run channel), taps coexist: any number of observers — SSE
        streams, host aggregators — can tap one session without displacing
        the client or each other. ``replay=True`` first delivers the events
        of the in-progress message so a mid-turn tap sees a coherent view.

        ``live_partial=True`` additionally delivers the *unsealed* streaming
        state — partial assistant text, partial tool input, the latest
        workflow progress — which no replay path can reach because those
        types are never persisted. The two flags are independent: a caller
        that runs its own durable backfill wants ``live_partial`` alone.
        """
        bus = self._get_or_create_bus(session_id)
        replay_events = await self._build_replay(user_id, session_id) if replay else []
        await bus.add_tap(sink, replay=replay_events, live_partial=live_partial)

    async def detach_session_tap(self, session_id: str, sink: EventSink) -> None:
        """Unregister a tap added via :meth:`attach_session_tap`."""
        bus = self._buses.get(session_id)
        if bus is not None:
            await bus.remove_tap(sink)

    def attach_global_tap(self, tap: GlobalEventTap) -> None:
        """Register a process-wide tap receiving ``(session_id, event)``
        for every event on every session bus.

        Intended for singleton host-level aggregators (decision inbox,
        remote event streams). Synchronous on purpose — registration is a
        list append on the shared tap list, effective immediately for all
        existing and future buses.
        """
        self._global_taps.append(tap)

    def detach_global_tap(self, tap: GlobalEventTap) -> None:
        try:
            self._global_taps.remove(tap)
        except ValueError:
            pass

    async def emit_session_event(
        self, session_id: str, event: Event, *, create_bus: bool = False
    ) -> None:
        """Emit an event onto a session's bus from outside a turn.

        Used by the API layer for session-state notifications that are not
        tied to a Message — e.g., the ``mode_changed`` event fired from
        ``POST /sessions/{id}/mode``. If no bus exists yet (no client has
        ever attached and no turn has ever run), this is a no-op: the
        authoritative state lives on the ``Session`` row, and the event
        is purely a live-notification channel for currently-attached
        clients. No DB persistence — by design (see
        ``docs/design/session-modes.md`` §Events).

        ``create_bus=True`` forces bus creation so the event reaches
        global taps (and any tap registered between turns) even when no
        client has ever attached — used for synthetic notifications like
        the interrupt-fallback ``session_error``.
        """
        if create_bus:
            bus: SessionEventBus | None = self._get_or_create_bus(session_id)
        else:
            bus = self._buses.get(session_id)
        if bus is None:
            return
        await bus.emit(event)

    async def attach_session_sink(self, user_id: str, session_id: str, sink: EventSink) -> None:
        """Subscribe ``sink`` to this session's live event stream.

        If a turn is currently in flight, replays the events of the
        in-progress message first so the new client sees a coherent
        view of the run-so-far. Subsequent live emits arrive in order.
        """
        bus = self._get_or_create_bus(session_id)
        replay = await self._build_replay(user_id, session_id)
        await bus.attach(sink, replay=replay)

    async def detach_session_sink(self, session_id: str, sink: EventSink) -> None:
        """Unsubscribe ``sink``. Does not affect the running turn."""
        bus = self._buses.get(session_id)
        if bus is not None:
            await bus.detach(sink)

    async def _build_replay(self, user_id: str, session_id: str) -> list[Event]:
        """Replay = events of any message still in ``running`` status.

        We don't replay finalized history — REST handles that via
        ``GET /sessions/{id}/messages``. The bus only needs to fill the
        gap for the turn that's still emitting live events.

        DB stores raw events (no ``message_id`` field in ``data``); the
        live emit path stamps them on the way to the WS via
        :class:`_MessageIdStampSink`. Replay must stamp consistently so
        the client routes them to the right ``MessageView``.
        """
        active_message = self._active_message.get(session_id)
        if active_message is None:
            return []
        raw_events = await self._store.get_events_for_message(user_id, active_message.id)
        message_id = active_message.id
        return [
            Event(
                type=ev.type,
                data={**ev.data, "message_id": message_id},
                timestamp=ev.timestamp,
            )
            for ev in raw_events
        ]

    async def run_turn(
        self,
        user_id: str,
        session_id: str,
        user_message: UserMessage,
    ) -> Message:
        """Execute one conversation turn.

        Loads project and agent config from the session's bindings, creates
        a Message row for this run, then delegates to the runtime with the
        project's cwd as workspace root. The Message is finalized — with
        terminal status, assistant text, error payload, and stop reason —
        before this method returns.

        Outbound events flow through the session's :class:`SessionEventBus`,
        which forwards to whichever client sink is currently attached
        (or none). The DatabaseEventSink in the same composite ensures
        every event is persisted regardless of client state — this is
        what makes reconnect-with-replay correct.
        """
        from src.adapters.database_sink import DatabaseEventSink
        from src.adapters.delta_coalescing_sink import DeltaCoalescingSink
        from src.adapters.persist_then_broadcast_sink import PersistThenBroadcastSink

        session, agent = await self._load_session(user_id, session_id)

        # Slice 3 of session-modes (broadened in slice 6 simplification):
        # both Claude and Codex process ``/plan <text>`` / ``/goal <text>``
        # in their user-input stream — Claude's CLI intercepts the slash,
        # codex's app-server interprets it as a per-turn mode marker.
        # ``wrap_for_mode`` prepends the matching slash so each turn in a
        # non-default mode enters the native mode for that turn. The
        # exceptions (Claude+plan toggle, DeepAgents no-primitive,
        # user-supplied slashes) are spelled out in ``wrap_for_mode``'s
        # docstring. The wrapped form is what gets persisted on the
        # ``Message`` row — source of truth is what the runtime saw, so
        # replay is correct without re-wrapping on read.
        wrapped_text = wrap_for_mode(user_message.text, session.mode, session.runtime_provider)
        if wrapped_text != user_message.text:
            user_message = dataclasses.replace(user_message, text=wrapped_text)

        if _session_citation_enabled(session):
            scope_context = _citation_output_scope_context(user_message.text)
            if scope_context:
                existing_context = user_message.additional_context.strip()
                user_message = dataclasses.replace(
                    user_message,
                    additional_context=(
                        f"{existing_context}\n\n{scope_context}"
                        if existing_context
                        else scope_context
                    ),
                )

        message = Message(
            id=str(uuid.uuid4()),
            session_id=session_id,
            user_message=user_message,
            started_at=now_ms(),
            status="running",
        )
        await self._store.save_message(user_id, message)
        self._active_message[session_id] = message

        # Persist ``session.status = "running"`` so the DB row reflects
        # the in-flight state for the duration of the turn. Before this,
        # ``status="running"`` was set in-memory by each runtime at
        # ``run()`` entry but only saved back as ``"idle"`` at end of
        # turn — DB never observed a real "running" row, and
        # ``list_sessions(status="running")`` returned nothing in normal
        # operation. A host crash mid-turn now leaves a real orphan
        # ``running`` row for ``scan_orphan_runs`` to reset on the next
        # startup. The defensive reset in ``finally`` below covers the
        # narrower case where ``run()`` returns without restoring
        # ``status`` (all current runtimes do restore it, but the path
        # is defensive against a future runtime regression).
        session.status = "running"
        await self._store.save_session(session)

        bus = self._get_or_create_bus(session_id)
        bus_sink: EventSink = _MessageIdStampSink(bus, message.id)
        db_sink = DatabaseEventSink(self._store, user_id, session_id, message.id)
        # Persist FIRST, then broadcast with the row id stamped into
        # ``data["seq"]`` — live frames of persisted events carry stable
        # storage coordinates so stream consumers can deduplicate the
        # backfill/live boundary exactly. Live-only delta types skip the
        # DB and flow straight through (no added latency on the token
        # streaming path).
        persist_then_live: EventSink = PersistThenBroadcastSink(db_sink, bus_sink)
        # Coalesce per-token deltas into ~30ms batches before the
        # persist→broadcast pipeline. Reduces WS frame count and DB row
        # count without changing the canonical assistant_message/thinking
        # record.
        coalesced: EventSink = DeltaCoalescingSink(persist_then_live)
        document_scope = _session_document_scope(session)
        no_research_scope = (
            document_scope is None
            and not user_message.attachments
            and is_stable_general_knowledge_query(user_message.text)
        )
        citation_enabled = _session_citation_enabled(session) and not no_research_scope
        observer = _MessageObserverSink(
            coalesced,
            message_id=message.id,
            user_prompt=user_message.text,
            citation_policy_available=any(Path(path).name == "citation" for path in session.skills),
            citation_quality_policy=_session_citation_quality_policy(session),
            allowed_document_ids=document_scope,
            force_citation_required=(
                document_scope is not None and citation_enabled
            ),
            citation_enabled=citation_enabled,
            citation_verification_enabled=(
                _session_citation_verification_enabled(session) and not no_research_scope
            ),
        )

        # Sessions are self-sufficient: ``session.cwd`` is required at
        # creation. Seed the workspace stub lazily (idempotent, one stat on
        # the hot path) — there is no project-creation moment to hook.
        bootstrap_session_workspace(session.cwd, agent.name or None)
        runtime = await self._ensure_runtime(
            session_id,
            agent,
            session,
            observer,
            session.cwd,
        )
        self._active[session_id] = runtime

        try:
            await observer.emit(
                Event(
                    type="user_message",
                    data={
                        "message": user_message.text,
                        "attachments": [
                            {"source_path": a.source_path, "parsed_path": a.parsed_path}
                            for a in user_message.attachments
                        ],
                    },
                )
            )
            # The ``running`` flip above is persisted but was never announced:
            # the only ``session_update`` used to be the terminal one after the
            # turn. Clients that derive status from the event stream (session
            # header pill, control-plane ``run.status``) therefore sat on
            # ``created``/stale until end of turn. Emit the interim status here
            # so every follower — including per-turn re-subscribers on queue
            # drains — sees ``running`` the moment the turn actually starts.
            await observer.emit(
                Event(
                    type="session_update",
                    data={"status": "running", "message_id": message.id},
                )
            )
            await runtime.run(session, user_message)
            if (
                observer.citation_repair_requested
                and getattr(session.stop_reason, "type", None) == "end_turn"
            ):
                observer.begin_citation_repair()
                session.status = "running"
                # Hosts may still use this boundary to refresh their persisted
                # resource snapshot.  The repair itself never receives those
                # resources: all admissible evidence is sealed into its compact
                # prompt, so a hidden quality pass cannot start a second
                # research run or cross an expiring tool credential.
                if self._citation_repair_refresh_hook is not None:
                    try:
                        await self._citation_repair_refresh_hook(
                            user_id,
                            session_id,
                        )
                    except Exception:
                        logger.warning(
                            "citation repair credential refresh failed for session %s",
                            session_id,
                            exc_info=True,
                        )

                # A claim repair needs only the original request, sealed draft,
                # compact issue list, and bounded candidate evidence copied into
                # ``citation_repair_prompt``.  Never resume the full research
                # thread here: doing so replays tool schemas, skills, discovery
                # history and hundreds of thousands of tokens.  Instead, use a
                # fresh bare completion for every provider.  The user's real
                # session retains its native thread id, resources and history,
                # so later follow-ups still resume the original conversation.
                await self._evict_runtime(session_id)
                repair_metadata = copy.deepcopy(session.metadata)
                repair_metadata[BARE_COMPLETION_METADATA_KEY] = True
                repair_session = dataclasses.replace(
                    session,
                    instructions="",
                    skills=(),
                    mcp_servers=(),
                    mode="default",
                    status="running",
                    stop_reason=None,
                    metadata=repair_metadata,
                    runtime_session_id=None,
                    todos=None,
                )
                runtime = await self._ensure_runtime(
                    session_id,
                    agent,
                    repair_session,
                    observer,
                    repair_session.cwd,
                )
                self._active[session_id] = runtime
                logger.warning(
                    "citation_guard retrying message=%s session=%s",
                    message.id,
                    session.id,
                )
                try:
                    await runtime.run(
                        repair_session,
                        UserMessage(text=observer.citation_repair_prompt),
                    )
                    session.status = repair_session.status
                    session.stop_reason = repair_session.stop_reason
                finally:
                    await self._evict_runtime(session_id)
                    self._active.pop(session_id, None)
            await observer.ensure_partial_assistant_message()
            # finalize must run BEFORE save_session — it writes session.todos
            # (and message.todos) from the observer's last todo_update payload;
            # saving first would persist a stale snapshot.
            self._finalize_message(message, session, observer)
            # User-mutable fields (today: ``session.mode``) must survive a
            # mid-turn ``POST /mode``. The runtime holds the session by
            # reference and the unconditional ``save_session`` below would
            # otherwise clobber a parallel user write. Reconcile rule:
            #
            # * If the runtime explicitly emitted ``mode_changed{by:"runtime"}``
            #   during the turn (codex ``thread/goal/cleared`` listener or
            #   Claude bare-``/goal`` poll), the runtime's in-memory
            #   ``session.mode`` is the intended value — keep it.
            # * Otherwise reload from disk so any concurrent ``POST /mode``
            #   wins. The runtime didn't intend to change ``session.mode``;
            #   the in-memory value is just the snapshot from turn start.
            #
            # Only ``mode`` is reconciled here. Other runtime-owned fields
            # (``status``, ``stop_reason``, ``runtime_session_id``,
            # ``todos``) keep their in-memory values as before.
            if observer.runtime_mode_change is None:
                fresh = await self._store.load_session(user_id, session_id)
                if fresh is not None and fresh.mode != session.mode:
                    session.mode = fresh.mode
            await self._store.save_session(session)
            await self._store.save_message(user_id, message)
            await observer.emit(
                Event(
                    type="session_update",
                    data={"status": session.status, "message_id": message.id},
                )
            )
            return message
        finally:
            self._active.pop(session_id, None)
            self._active_message.pop(session_id, None)
            # Mark the runtime freshly-used at turn END too, not just at entry.
            # A long-running turn (in ``_active``, so never swept) could finish
            # well past the idle TTL measured from its start; without this bump
            # the very next sweep would evict a runtime that just went idle.
            if session_id in self._runtimes:
                self._runtime_last_used[session_id] = time.monotonic()
            # Defensive: if ``run()`` returned (or raised) without
            # resetting ``session.status``, force it back to ``"idle"``
            # so the DB doesn't carry a phantom ``running`` row from a
            # normal cleanup. Host crashes (SIGKILL / power loss) skip
            # this branch entirely — those orphans are intentionally
            # left for ``scan_orphan_runs`` to clean up on next startup.
            if session.status == "running":
                session.status = "idle"
                try:
                    await self._store.save_session(session)
                except Exception:
                    logger.exception(
                        "orchestrator: defensive status reset save_session failed for %s",
                        session_id,
                    )

    def active_message_id(self, session_id: str) -> str | None:
        message = self._active_message.get(session_id)
        return message.id if message is not None else None

    @staticmethod
    def _finalize_message(
        message: Message,
        session: Session,
        observer: _MessageObserverSink,
    ) -> None:
        message.ended_at = now_ms()
        message.assistant_message = observer.assistant_text
        if observer.citation_bundle is not None:
            message.metadata = {
                **message.metadata,
                "citation_bundle": observer.citation_bundle,
            }
        message.total_turns = observer.num_turns or 1
        message.stop_reason = session.stop_reason
        if observer.usage is not None:
            message.input_tokens = observer.usage["input_tokens"]
            message.output_tokens = observer.usage["output_tokens"]
            message.cache_read_tokens = observer.usage["cache_read_tokens"]
            message.cache_write_tokens = observer.usage["cache_write_tokens"]
        if observer.model_usage is not None:
            message.model_usage = observer.model_usage
        if observer.last_todos is not None:
            # change-only semantics: this turn's snapshot lands on Message,
            # and Session carries the live latest. UI does carry-forward.
            message.todos = list(observer.last_todos)
            session.todos = list(observer.last_todos)
        if isinstance(session.stop_reason, Error):
            message.status = (
                "cancelled" if session.stop_reason.category == "user_interrupt" else "errored"
            )
            message.error_message = observer.error_payload or {
                "category": session.stop_reason.category,
                "message": session.stop_reason.message,
            }
        else:
            message.status = "completed"

    async def interrupt(self, session_id: str) -> bool:
        runtime = self._active.get(session_id)
        if runtime is None:
            return False
        await runtime.interrupt()
        return True

    async def cleanup(self, session_id: str) -> None:
        self._active.pop(session_id, None)
        self._buses.pop(session_id, None)
        # Session-scoped approval rules are tied to the runtime's lifecycle —
        # clearing here means a cold-reload (PATCH that drops the cache,
        # process restart, explicit cleanup) starts fresh. Matches codex's
        # native ``tool_approvals`` non-persistence behavior; see
        # ``docs/design/approve-for-session.md`` §8.
        self._session_approval_cache.clear(session_id)
        runtime = self._runtimes.pop(session_id, None)
        self._runtime_last_used.pop(session_id, None)
        if runtime is not None:
            try:
                await runtime.close()
            except Exception:
                logger.debug("Error closing runtime for session %s", session_id, exc_info=True)

    async def _load_session(self, user_id: str, session_id: str) -> tuple[Any, AgentConfig]:
        session = await self._store.load_session(user_id, session_id)
        if session is None:
            raise SessionNotFoundError(session_id)
        # The embedded snapshot IS the agent for this session — the kernel
        # holds no agents table to consult.
        return session, session.agent_config

    async def _ensure_runtime(
        self,
        session_id: str,
        agent: AgentConfig,
        session: Any,
        sink: EventSink,
        workspace_root: str,
    ) -> RuntimePort:
        from src.runtimes.factory import create_runtime

        # Opportunistic eviction on every turn entry: close runtimes idle past
        # the TTL (skipping the one we're about to touch / any active turn).
        # This is the lazy half of the policy — the background sweeper covers
        # the zero-activity case; together they bound the live subprocess set.
        await self._sweep_idle_runtimes(exclude=session_id)

        cached = self._runtimes.get(session_id)
        if cached is not None:
            cached.update_sink(sink)
            self._runtime_last_used[session_id] = time.monotonic()
            return cached

        runtime = create_runtime(agent, session, sink, workspace_root=workspace_root)
        # Inject a session-rule finder so runtimes that wire
        # ``approve_for_session`` can consult the kernel-owned cache
        # before parking on the user. Implemented via duck-typed setter
        # rather than a Protocol method so runtimes that haven't wired
        # the verb yet (codex, claude in Phase 1) don't need a no-op
        # implementation. Phase 2 / 3 will lift the setter onto
        # ``RuntimePort`` once all three runtimes consume it.
        setter = getattr(runtime, "set_session_rule_finder", None)
        if callable(setter):
            setter(self._build_session_rule_finder(session_id, runtime))
        self._runtimes[session_id] = runtime
        self._runtime_last_used[session_id] = time.monotonic()
        # Enforce the hard LRU ceiling after admitting the new runtime. This is
        # the guaranteed bound on concurrent warm subprocesses, independent of
        # the TTL: no matter how many sessions are touched, at most
        # ``_max_warm_runtimes`` claude/codex processes stay alive at once.
        await self._enforce_runtime_cap(exclude=session_id)
        return runtime

    # ── Warm-runtime eviction (idle TTL + LRU cap) ─────────────────────────

    def start(self) -> None:
        """Start the background idle-sweeper. Idempotent; requires a running
        event loop (call from the composition root's async init). Eviction is
        still correct without it — the lazy sweep in ``_ensure_runtime`` runs
        on every turn — this just covers sessions that go idle with no further
        activity anywhere. No-op when the idle TTL is disabled (``<= 0``)."""
        if self._runtime_idle_ttl_s <= 0 or self._sweep_interval_s <= 0:
            return
        if self._sweeper_task is not None and not self._sweeper_task.done():
            return
        self._closing = False
        self._sweeper_task = asyncio.create_task(self._run_sweeper())

    async def shutdown(self) -> None:
        """Cancel the sweeper and close every cached runtime — i.e. terminate
        all live claude/codex subprocesses deterministically on host shutdown,
        rather than relying on the SDKs' atexit reaper. Called from
        ``app.dependencies.shutdown_dependencies``."""
        self._closing = True
        task = self._sweeper_task
        self._sweeper_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        for session_id in list(self._runtimes):
            await self._evict_runtime(session_id)

    async def _run_sweeper(self) -> None:
        """Periodic idle sweep loop. Resilient: a failing sweep is logged and
        the loop continues; cancellation (shutdown) propagates."""
        try:
            while not self._closing:
                await asyncio.sleep(self._sweep_interval_s)
                if self._closing:
                    break
                try:
                    await self._sweep_idle_runtimes()
                except Exception:  # noqa: BLE001 — a bad sweep must not kill the loop
                    logger.debug("runtime idle sweep failed", exc_info=True)
        except asyncio.CancelledError:
            raise

    def _has_live_background_tasks(self, session_id: str) -> bool:
        """Duck-typed busy signal: the claude runtime exposes
        ``has_live_background_tasks`` while a ``run_in_background`` process it
        spawned is still running (the process is a child of the CLI subprocess,
        so eviction would kill the user's work mid-task). Runtimes without the
        attribute are never bg-busy."""
        runtime = self._runtimes.get(session_id)
        return bool(getattr(runtime, "has_live_background_tasks", False))

    def bg_busy_session_ids(self) -> list[str]:
        """Session ids of warm runtimes with live background tasks.

        Process-scoped (the orchestrator holds no owner index) — callers
        intersect with their own owner-scoped session set. The host's
        activity overview uses this so a session whose turn ended but whose
        ``run_in_background`` process is still running keeps signalling
        in-flight work."""
        return [sid for sid in self._runtimes if self._has_live_background_tasks(sid)]

    async def _sweep_idle_runtimes(self, *, exclude: str | None = None) -> None:
        """Close every cached runtime untouched for longer than the idle TTL.
        Never evicts an active turn (``_active``) — a parked approval keeps the
        session active because ``runtime.run()`` is still awaiting, so this also
        protects sessions waiting on a user decision. Runtimes with live
        background tasks get the extended ``bg_busy_runtime_ttl_s`` instead of
        the normal TTL (see the constant's comment for the rationale)."""
        if self._runtime_idle_ttl_s <= 0:
            return
        now = time.monotonic()
        stale: list[str] = []
        for sid, ts in list(self._runtime_last_used.items()):
            if sid == exclude or sid in self._active:
                continue
            ttl = self._runtime_idle_ttl_s
            if self._has_live_background_tasks(sid):
                if self._bg_busy_runtime_ttl_s <= 0:
                    continue  # full exemption
                ttl = max(ttl, self._bg_busy_runtime_ttl_s)
            if (now - ts) >= ttl:
                stale.append(sid)
        for sid in stale:
            await self._evict_runtime(sid)

    async def _enforce_runtime_cap(self, *, exclude: str | None = None) -> None:
        """Evict least-recently-used runtimes until the warm set is within the
        cap. Skips active turns and runtimes with live background tasks; if
        every over-cap entry is protected, the cap is briefly exceeded rather
        than tearing down a running subprocess (or killing background work).
        The extended-TTL sweep remains the backstop that unwinds a prolonged
        excess."""
        if self._max_warm_runtimes <= 0:
            return
        if len(self._runtimes) <= self._max_warm_runtimes:
            return
        evictable = sorted(
            (
                sid
                for sid in self._runtimes
                if sid != exclude
                and sid not in self._active
                and not self._has_live_background_tasks(sid)
            ),
            key=lambda s: self._runtime_last_used.get(s, 0.0),
        )
        for sid in evictable:
            if len(self._runtimes) <= self._max_warm_runtimes:
                break
            await self._evict_runtime(sid)

    async def _evict_runtime(self, session_id: str) -> None:
        """Drop a runtime from the warm cache and close it (kills its CLI
        subprocess). Keeps the session's event bus so an attached client keeps
        streaming and the next turn rebuilds the runtime (resuming via the
        persisted ``runtime_session_id``) — a cold reload, hence the approval
        cache is cleared to match ``cleanup`` semantics. Use ``cleanup`` (not
        this) when the session itself is going away."""
        runtime = self._runtimes.pop(session_id, None)
        self._runtime_last_used.pop(session_id, None)
        self._session_approval_cache.clear(session_id)
        if runtime is None:
            return
        try:
            await runtime.close()
        except Exception:  # noqa: BLE001
            logger.debug("Error evicting runtime for session %s", session_id, exc_info=True)
        else:
            logger.info("Evicted warm runtime for idle/over-cap session %s", session_id)

    def _build_session_rule_finder(
        self,
        session_id: str,
        runtime: RuntimePort,
    ) -> SessionRuleFinder:
        """Close over ``(session_id, cache, runtime.approval_rule_matcher)``
        so the runtime can check the cache without a backref to the
        orchestrator. Matcher is per-runtime — its ``match`` is the only
        code path that interprets ``rule_data``."""
        cache = self._session_approval_cache
        matcher = runtime.approval_rule_matcher

        def find(
            subject: str,
            tool_name: str,
            args: dict[str, Any],
            runtime_extras: dict[str, Any],
        ) -> SessionRule | None:
            return cache.find_match(session_id, subject, tool_name, args, runtime_extras, matcher)

        return find

    # ── Approval contract (Phase 1 / Slice 2) ──────────────────────────

    async def submit_action(
        self,
        user_id: str,
        session_id: str,
        pending_id: str,
        decision: Literal[
            "approve", "approve_with_changes", "approve_for_session", "reject", "answer"
        ],
        message: str | None = None,
        answers: dict[str, str | list[str]] | None = None,
        modified_input: dict[str, Any] | None = None,
    ) -> SubmitActionResult:
        """Resolve a pending ``requires_action`` event.

        Validation order (raises one of the typed errors below):
          1. Session loadable (else SessionNotFoundError)
          3. ``pending_id`` matches a ``requires_action`` event
             (else PendingActionNotFoundError)
          4. Decision matches the pending's subject and the pending's
             ``available_decisions``:
             - ``answer`` is only valid for ``clarifying_questions``,
               and that subject rejects bare ``approve`` /
               ``approve_with_changes`` (Claude SDK needs the
               structured ``answers`` payload).
             - ``approve_with_changes`` is only valid for tool-approval
               subjects on runtimes that expose the verb in
               ``available_decisions`` (Claude / DeepAgents); codex
               pendings reject it because their SDK has no
               ``updated_input`` analog.
             - ``approve_for_session`` requires the pending to
               advertise the verb in ``available_decisions`` AND to
               carry a ``session_rule_preview`` field populated by the
               runtime's matcher at emit time. Missing preview is a
               400 (runtime mis-wired). See
               ``docs/design/approve-for-session.md`` §3.2.
             Mismatch → PendingActionDecisionMismatchError.
          5. Pending not already sealed
             - same decision → idempotent 200 with original timestamp
             - different decision → PendingActionConflictError
             - ``expired`` / ``interrupted`` → PendingActionExpiredError
          6. A runtime must be parked on this approval (turn in flight)
             (else RuntimeUnavailableError)
          7. For ``approve_for_session``: commit the rule to the kernel
             cache, then forward to the runtime as plain ``approve`` —
             the rule lifecycle is kernel-owned, the runtime only sees
             SDK-mappable verbs.
          8. Forward decision to the runtime; if the runtime hasn't wired
             the bridge yet, raise ApprovalNotImplementedError so the
             route can surface 501 instead of 500
          9. Emit ``action_resolved`` (DB + bus) — includes ``answers``
             when ``decision == "answer"``, ``modified_input`` when
             ``decision == "approve_with_changes"``, and ``rule_id``
             when ``decision == "approve_for_session"`` so reconnects
             can replay the complete decision.
        """
        session = await self._store.load_session(user_id, session_id)
        if session is None:
            raise SessionNotFoundError(session_id)

        pending_event, resolved_event = await self._derive_pending(user_id, session_id, pending_id)
        if pending_event is None:
            raise PendingActionNotFoundError(pending_id)

        # Subject ↔ decision invariant. We treat this as a 400 rather than
        # a 409 because it's a contract violation (wrong shape for this
        # pending), not a legitimate race between two clients.
        pending_subject = str(pending_event.data.get("subject", ""))
        if pending_subject == "clarifying_questions":
            if decision not in ("answer", "reject"):
                raise PendingActionDecisionMismatchError(pending_id, pending_subject, decision)
        elif decision == "answer":
            raise PendingActionDecisionMismatchError(pending_id, pending_subject, decision)
        # ``approve_with_changes`` is per-pending — only Claude / DeepAgents
        # advertise it in ``available_decisions``. Codex emits the V1 baseline
        # so its pendings reject the verb here. Reading from the pending
        # event keeps the runtime as the source of truth — orchestrator
        # doesn't duplicate the SDK capability matrix.
        if decision == "approve_with_changes":
            allowed = pending_event.data.get("available_decisions") or []
            if "approve_with_changes" not in allowed:
                raise PendingActionDecisionMismatchError(pending_id, pending_subject, decision)
        # ``approve_for_session`` follows the same available_decisions gate
        # and additionally requires ``session_rule_preview`` on the pending
        # (the runtime's matcher fills this in when emitting). Missing
        # preview = runtime wired the verb without the preview — a 400
        # contract violation, not a 409 race.
        if decision == "approve_for_session":
            allowed = pending_event.data.get("available_decisions") or []
            if "approve_for_session" not in allowed:
                raise PendingActionDecisionMismatchError(pending_id, pending_subject, decision)
            preview = pending_event.data.get("session_rule_preview")
            if not isinstance(preview, dict):
                raise PendingActionDecisionMismatchError(pending_id, pending_subject, decision)

        if resolved_event is not None:
            previous = str(resolved_event.data.get("decision", ""))
            if previous in ("expired", "interrupted"):
                raise PendingActionExpiredError(pending_id, previous)
            if previous == decision:
                # Idempotent replay surfaces the original rule_id so the
                # client can re-discover the rule it created (e.g. WS
                # reconnect after the user double-clicked the button).
                prior_rule_id = resolved_event.data.get("rule_id")
                return SubmitActionResult(
                    pending_id=pending_id,
                    decision=decision,
                    accepted_at=resolved_event.timestamp,
                    idempotent=True,
                    rule_id=str(prior_rule_id) if isinstance(prior_rule_id, str) else None,
                )
            raise PendingActionConflictError(pending_id, previous, decision)

        runtime = self._runtimes.get(session_id)
        active_message = self._active_message.get(session_id)
        if runtime is None or active_message is None:
            # Pending exists in events but the runtime is gone — typical
            # cause: host restart, but startup scan should have sealed
            # the row first. Surface as 400 so the client refetches.
            raise RuntimeUnavailableError(session_id)

        # ``approve_for_session`` commits the rule kernel-side BEFORE
        # talking to the runtime — that way a runtime-side failure
        # leaves no orphaned rule, and the next matching call's cache
        # check sees the rule. The runtime always sees plain ``approve``
        # at its boundary (it has no SDK verb for session persistence;
        # see §5 of the design doc).
        committed_rule: SessionRule | None = None
        if decision == "approve_for_session":
            preview = pending_event.data["session_rule_preview"]
            committed_rule = SessionRule(
                rule_id=str(uuid.uuid4()),
                session_id=session_id,
                originating_pending_id=pending_id,
                subject=pending_subject,  # type: ignore[arg-type]
                runtime_kind=str(preview.get("runtime_kind", "exact")),
                display=str(preview.get("display", "")),
                rule_data=dict(preview.get("rule_data") or {}),
                created_at=now_ms(),
            )
            self._session_approval_cache.put(committed_rule)

        # Translate ``approve_for_session`` → ``approve`` at the runtime
        # boundary. The runtime's ``submit_action`` Literal does not
        # include the session verb (kernel-only).
        runtime_decision: Literal["approve", "approve_with_changes", "reject", "answer"]
        if decision == "approve_for_session":
            runtime_decision = "approve"
        else:
            runtime_decision = decision
        try:
            await runtime.submit_action(
                pending_id, runtime_decision, message, answers, modified_input
            )
        except NotImplementedError as exc:  # noqa: PERF203 — single-handler
            raise ApprovalNotImplementedError(str(exc)) from exc

        message_id = active_message.id
        resolved_data: dict[str, Any] = {
            "pending_id": pending_id,
            "decision": decision,
            "message": message,
            "resolved_by": "user",
        }
        # Payload-carrying verbs persist their payload on the event so
        # reconnect can replay the complete decision shape. Synthetic
        # emits (expired / interrupted) never carry these, mirroring
        # the bare reject case.
        if decision == "answer" and answers is not None:
            resolved_data["answers"] = answers
        if decision == "approve_with_changes" and modified_input is not None:
            resolved_data["modified_input"] = modified_input
        if committed_rule is not None:
            resolved_data["rule_id"] = committed_rule.rule_id
        resolved = Event(type="action_resolved", data=resolved_data)
        await self._store.append_event(user_id, session_id, message_id, resolved)
        bus = self._get_or_create_bus(session_id)
        await bus.emit(
            Event(
                type=resolved.type,
                data={**resolved.data, "message_id": message_id},
                timestamp=resolved.timestamp,
            )
        )
        return SubmitActionResult(
            pending_id=pending_id,
            decision=decision,
            accepted_at=resolved.timestamp,
            idempotent=False,
            rule_id=committed_rule.rule_id if committed_rule is not None else None,
        )

    # ── Internal helpers for runtime auto-approve flow ─────────────────

    @property
    def session_approval_cache(self) -> SessionApprovalCache:
        """Read-only access to the kernel-owned cache. Exposed primarily
        for tests; production runtimes consult the cache via the
        ``SessionRuleFinder`` injected by ``_ensure_runtime``."""
        return self._session_approval_cache

    async def _derive_pending(
        self, user_id: str, session_id: str, pending_id: str
    ) -> tuple[Event | None, Event | None]:
        """Return ``(requires_action, action_resolved)`` for ``pending_id``.

        Linear scan over the session's events log. Per design doc §4.4
        pending state is a derived view over events rather than a parallel
        table; for v1 the read path is good enough at low session
        cardinality.
        """
        pending: Event | None = None
        resolved: Event | None = None
        # Filter to the two pending markers at the store. A ``requires_action``
        # is the MOST RECENT event when it is resolved, so an unfiltered
        # oldest-first read (this used to cap at ``limit=1000, offset=0``)
        # silently dropped it in any session with >N events and ``submit_action``
        # then 404'd a live approval. The type filter makes the read
        # O(pendings), not O(session length), so length no longer matters.
        events = await self._store.get_events(
            user_id,
            session_id,
            types=("requires_action", "action_resolved"),
            limit=1000,
        )
        for ev in events:
            if ev.data.get("pending_id") != pending_id:
                continue
            if ev.type == "requires_action" and pending is None:
                pending = ev
            elif ev.type == "action_resolved" and resolved is None:
                resolved = ev
        return pending, resolved

    async def scan_orphan_pendings(self) -> int:
        """Seal every still-open ``requires_action`` with a synthetic
        ``action_resolved(decision="expired", resolved_by="system")``.

        Called on host startup (per design doc §6.3) — pending approvals
        do not survive a host process restart in v1; the contract is
        uniform across runtimes even though DeepAgents could technically
        do better. Returns the number of synthetic resolutions emitted.
        """
        sealed = 0
        # Own-lineage sweep: ``self._store`` reads are the kernel's runtime
        # sqlite (RuntimeStore authority) — sessions live on other processes
        # are structurally out of reach, so this is safe in every deployment.
        # ``user_id=None`` spans every owner within this kernel's own store.
        sessions = await self._store.list_sessions(None, status="running", limit=500)
        for session in sessions:
            sealed += await self._seal_session_pendings(session.user_id, session.id)
        return sealed

    async def _seal_session_pendings(self, user_id: str, session_id: str) -> int:
        """Seal one session's open ``requires_action`` events (see
        ``src.core.recovery.seal_session_pendings``) on this kernel's store."""
        return await recovery.seal_session_pendings(self._store, user_id, session_id)

    async def scan_orphan_runs(self) -> int:
        """On host startup, reset sessions left in ``status="running"``.

        These are turns the previous host process started (``run_turn``
        writes ``status="running"`` before calling the runtime, since
        the 2026-05 in-flight-status change) but never got to flip
        back to ``idle`` because the process was killed mid-turn. We:

        1. Set ``session.status = "idle"`` + ``stop_reason =
           Error(category="host_restart", ...)`` so the UI's session
           chip stops showing a phantom running indicator.
        2. Walk the session's messages and mark any
           ``Message.status == "running"`` row as ``"errored"`` with a
           ``host_restart`` ``error_message`` and ``ended_at = now`` —
           otherwise history reads would render a perpetual spinner.

        Pairs with ``scan_orphan_pendings`` (which seals any
        ``requires_action`` events still open on the same orphan
        turns). Both run from ``app/dependencies.py`` on startup. The
        ``status="idle"`` -> ``"running"`` -> ``"idle"`` cycle in a
        healthy turn never trips this scanner because the live
        ``run_turn`` ``finally`` block resets the status before save
        in the normal cleanup path. Only a true crash (SIGKILL /
        power loss / OOM) leaves the row behind.

        Returns the number of sessions reset.
        """
        reset = 0
        # Own-lineage sweep (see scan_orphan_pendings). Sessions stranded on
        # OTHER processes are the HOST's to reconcile (liveness-checked
        # ``reset_stranded_session``) — never this kernel's.
        sessions = await self._store.list_sessions(None, status="running", limit=500)
        for session in sessions:
            await self._reset_stranded(session)
            reset += 1
        return reset

    async def _reset_stranded(self, session: Session) -> None:
        """Reset one stranded session (see ``src.core.recovery.reset_stranded``)
        on this kernel's store."""
        await recovery.reset_stranded(self._store, session)

    async def reset_stranded_session(self, user_id: str, session_id: str) -> bool:
        """Per-session stranded reset on this kernel's own store (see
        ``src.core.recovery.reset_stranded_session`` — the host applies the
        same semantics to its durable for sessions whose sandbox is gone)."""
        return await recovery.reset_stranded_session(self._store, user_id, session_id)
