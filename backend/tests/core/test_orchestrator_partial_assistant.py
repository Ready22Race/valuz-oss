"""Partial assistant history when a turn is interrupted mid-stream."""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

import json

import valuz_agent.boot.kernel  # noqa: F401

from src.adapters.database_sink import DatabaseEventSink
from src.adapters.delta_coalescing_sink import DeltaCoalescingSink
from src.adapters.persist_then_broadcast_sink import PersistThenBroadcastSink
from src.core.events import Event
from src.core.orchestrator import (
    _MessageObserverSink,
    _attach_standalone_citation_lines,
    _sanitize_citation_repair_prose,
    _strip_empty_markdown_labels,
    _strip_empty_markdown_tables,
    _strip_unrequested_derived_restatement,
    _strip_unrequested_cross_period_recap,
    _strip_unrequested_period_leadin,
    _strip_leading_assistant_progress,
    _strip_strict_scope_leadin,
    _strip_strict_table_trailing_blocks,
    _strip_unrequested_source_excerpt,
    _strip_unrequested_retrieval_internals,
)


def test_strict_table_answer_drops_unrequested_formula_recap() -> None:
    text = (
        "| 指标 | 数值 | 单位 | 期间 |\n"
        "|---|---|---|---|\n"
        "| 2024 年营业收入 | 1,708.99 亿元 [1](citation://cit_a) | 人民币 | 2024 FY |\n"
        "| 2023 年营业收入 | 1,476.94 亿元 [2](citation://cit_b) | 人民币 | 2023 FY |\n"
        "| 同比增速 | +15.71% [3](citation://cit_c) | — | 2024 vs 2023 |\n\n"
        "同比增速 = (170899152276 - 147693604994) / 147693604994 = +15.71%。"
    )

    result = _strip_strict_table_trailing_blocks(
        text,
        "只输出 2024 年营业收入、2023 年营业收入、同比增速，注明单位和期间。",
    )

    assert result == "\n".join(text.splitlines()[:5])


def test_blockquote_citation_only_line_attaches_to_the_quoted_claim() -> None:
    text = (
        "> \"Demand continues to increase.\"\n"
        "> [source](evidence://ev_quote_12345678)\n\n"
        "## Next section\n"
        "> [source](evidence://ev_decorative_12345678)"
    )

    assert _attach_standalone_citation_lines(text) == (
        "> \"Demand continues to increase.\" "
        "[source](evidence://ev_quote_12345678)\n\n"
        "## Next section\n"
        "> [source](evidence://ev_decorative_12345678)"
    )


def test_cited_introduction_attaches_source_to_following_blockquote() -> None:
    text = (
        "与 OpenAI 的合作范围包括持续训练与推理能力 "
        "[source](evidence://ev_openai_12345678)：\n\n"
        "> \"The partnership runs through 2030 and includes models through 2032.\""
    )

    assert _attach_standalone_citation_lines(text) == (
        "与 OpenAI 的合作范围包括持续训练与推理能力 "
        "[source](evidence://ev_openai_12345678)：\n\n"
        "> \"The partnership runs through 2030 and includes models through 2032.\" "
        "[source](evidence://ev_openai_12345678)"
    )


def test_trailing_table_citations_distribute_one_per_data_row() -> None:
    text = (
        "| 指标 | 数值 | 单位 | 期间 |\n"
        "|------|------|------|------|\n"
        "| 2024 年营业收入 | 1,708.99 亿元 | 人民币 | 2024 财年 |\n"
        "| 2023 年营业收入 | 1,476.94 亿元 | 人民币 | 2023 财年 |\n"
        "| 同比增速 | +15.71% | — | 2024 vs 2023 |\n\n"
        "[source](evidence://evc_current_12345678#/data/1/revenue) "
        "[source](evidence://evc_prior_12345678#/data/0/revenue) "
        "[source](evidence://ev_calc_growth_12345678)"
    )

    result = _attach_standalone_citation_lines(text)

    lines = result.splitlines()
    assert "evc_current_12345678#/data/1/revenue" in lines[2]
    assert "evc_prior_12345678#/data/0/revenue" in lines[3]
    assert "ev_calc_growth_12345678" in lines[4]
    assert len(lines) == 5


def test_empty_bold_section_labels_are_removed_without_touching_content_labels() -> None:
    text = (
        "### FY2026 Q1\n\n"
        "**AI 需求**\n\n"
        "**资本开支**：原文未披露具体数字。\n\n"
        "**供需约束**\n\n"
        "需求继续高于可用供给。"
    )

    assert _strip_empty_markdown_labels(text) == (
        "### FY2026 Q1\n\n"
        "**资本开支**：原文未披露具体数字。\n\n"
        "**供需约束**\n\n"
        "需求继续高于可用供给。"
    )


def test_empty_markdown_table_shell_is_removed_without_touching_real_table() -> None:
    text = (
        "**资本开支**\n\n"
        "| 指标 | 数据 |\n"
        "|---|---|\n\n"
        "**供需约束**\n\n"
        "| 季度 | 变化 |\n"
        "|---|---|\n"
        "| Q4 | 改善 |"
    )

    assert _strip_empty_markdown_tables(text) == (
        "**资本开支**\n\n"
        "**供需约束**\n\n"
        "| 季度 | 变化 |\n"
        "|---|---|\n"
        "| Q4 | 改善 |"
    )

def test_uncited_trailing_calculation_restatement_is_removed_unless_requested() -> None:
    text = (
        "归母净利率 = 862.28 / 1,708.99 = 50.46% "
        "[3](evidence://ev_calculation)\n\n"
        "即每实现 1 元营业收入，约有 0.50 元归属母公司股东。"
    )

    assert _strip_unrequested_derived_restatement(text, "展示公式和结果") == (
        "归母净利率 = 862.28 / 1,708.99 = 50.46% [3](evidence://ev_calculation)"
    )
    assert _strip_unrequested_derived_restatement(text, "解释这个结果的含义") == text


def test_cited_duplicate_calculation_recap_moves_citation_to_formula() -> None:
    text = (
        "**归母净利率计算：**\n\n"
        "$$归母净利率 = 862.28 / 1,708.99 = 50.46\\%$$\n\n"
        "归母净利率为 **50.46%** [3](evidence://ev_calculation)，"
        "即每 1 元营业收入约有 0.50 元归母利润。"
    )

    assert _strip_unrequested_derived_restatement(text, "展示公式和结果") == (
        "**归母净利率计算：**\n\n"
        "$$归母净利率 = 862.28 / 1,708.99 = 50.46\\%$$  "
        "[3](evidence://ev_calculation)"
    )


def test_unrequested_cross_period_recap_table_is_removed() -> None:
    text = (
        "### FY2026 Q1\n\n需求扩散 [1](evidence://ev_q1_12345678)。\n\n"
        "### FY2026 Q2\n\n新增容量 1 GW [1](evidence://ev_q2_12345678)。\n\n"
        "### 跨季度趋势概览\n\n"
        "| 维度 | Q1 | Q2 |\n|---|---|---|\n| 需求 | 扩散 | 加速 |\n\n"
        "注：本次来源块未披露其他数字。"
    )

    assert _strip_unrequested_cross_period_recap(
        text,
        "请按季度总结最近两个季度的电话会。",
    ) == (
        "### FY2026 Q1\n\n需求扩散 [1](evidence://ev_q1_12345678)。\n\n"
        "### FY2026 Q2\n\n新增容量 1 GW [1](evidence://ev_q2_12345678)。"
    )


def test_unrequested_core_theme_recap_table_is_removed() -> None:
    text = (
        "## FY2026 Q1\n\n需求扩散 [1](evidence://ev_q1_12345678)。\n\n"
        "## FY2026 Q2\n\n新增近 1 GW [2](evidence://ev_q2_12345678)。\n\n"
        "## 核心主线归纳\n\n"
        "| 维度 | FY26 Q1 | FY26 Q2 |\n|---|---|---|\n"
        "| 产能 | 扩张 | +1 GW |"
    )

    assert _strip_unrequested_cross_period_recap(
        text,
        "请总结最近两个季度的表述，并按季度引用原文。",
    ) == (
        "## FY2026 Q1\n\n需求扩散 [1](evidence://ev_q1_12345678)。\n\n"
        "## FY2026 Q2\n\n新增近 1 GW [2](evidence://ev_q2_12345678)。"
    )


def test_unrequested_horizontal_summary_is_removed() -> None:
    text = (
        "## FY2026 Q1\n\n需求扩散。\n\n"
        "## FY2026 Q2\n\n新增近 1 GW。\n\n"
        "## 横向小结\n\n| 维度 | Q1 | Q2 |\n|---|---|---|\n| 产能 | — | +1 GW |"
    )

    assert _strip_unrequested_cross_period_recap(
        text,
        "请总结最近两个季度的表述，并按季度引用原文。",
    ) == "## FY2026 Q1\n\n需求扩散。\n\n## FY2026 Q2\n\n新增近 1 GW。"


def test_requested_cross_period_recap_table_is_preserved() -> None:
    text = (
        "### FY2026 Q1\n\n需求扩散。\n\n"
        "### FY2026 Q2\n\n需求加速。\n\n"
        "### 跨季度趋势概览\n\n| 维度 | Q1 | Q2 |\n|---|---|---|\n| 需求 | 扩散 | 加速 |"
    )

    assert _strip_unrequested_cross_period_recap(
        text,
        "请按季度总结，并增加跨季度趋势表。",
    ) == text


def test_period_by_period_answer_starts_at_first_period_heading() -> None:
    text = (
        "以下是基于原始文本块的综合总结。\n\n"
        "## 最近季度概览\n\n"
        "> 覆盖季度：FY2026 Q1–Q2（2025年10月 → 2026年1月）\n\n"
        "### FY2026 Q1\n\n需求扩散。\n\n"
        "### FY2026 Q2\n\n需求加速。"
    )

    assert _strip_unrequested_period_leadin(
        text,
        "请按季度总结最近两个季度的电话会。",
    ) == "### FY2026 Q1\n\n需求扩散。\n\n### FY2026 Q2\n\n需求加速。"


def test_explicit_cross_period_overview_keeps_period_leadin() -> None:
    text = (
        "## 跨季度概览\n\n"
        "### FY2026 Q1\n\n需求扩散。\n\n"
        "### FY2026 Q2\n\n需求加速。"
    )

    assert _strip_unrequested_period_leadin(
        text,
        "请按季度总结并提供跨季度概览。",
    ) == text


def test_retrieval_block_narration_is_removed_without_dangling_emphasis() -> None:
    text = (
        "*本季度检索块以管理层开场陈述为主，具体资本开支指引数字"
        "未在本次来源块中披露。*\n\n结论。"
    )

    assert _strip_unrequested_retrieval_internals(
        text,
        "请总结电话会。",
    ) == "结论。"


class _FakeStore:
    def __init__(self) -> None:
        self.appended: list[Event] = []
        self._next_seq = 100

    async def append_event(
        self, user_id: str, session_id: str, message_id: str, event: Event, **kw: object
    ) -> int:
        self.appended.append(event)
        self._next_seq += 1
        return self._next_seq


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[Event] = []

    async def emit(self, event: Event) -> None:
        self.events.append(event)


def test_repair_prose_sanitizer_covers_internal_protocol_synonyms() -> None:
    for internal_term in (
        "evidenceHandle",
        "evidence handle",
        "citationId",
        "证据句柄",
        "引用句柄",
        "证据记录",
        "独立证据凭证",
        "合规绑定",
        "经认证的引用",
        "可引用来源",
        "行内引用",
        "嵌套财务子字段",
        "工具原始返回",
        "valuz.quality-claim.invalid",
        "[UNSOURCED]",
    ):
        result = _sanitize_citation_repair_prose(f"安全结论。\n\n诊断：{internal_term}。")
        assert result == "安全结论。"
        assert internal_term not in result


def test_repair_prose_sanitizer_keeps_fact_before_internal_diagnostic() -> None:
    result = _sanitize_citation_repair_prose(
        "扣非净利润：4.55亿元 [1](evidence://ev_profit_12345678)。"
        " evidence handle 已确认。\n"
        "商誉金额：34.41亿元 [2](evidence://ev_goodwill_12345678)。\n"
        "诊断：candidate evidence 不完整。"
    )

    assert "扣非净利润：4.55亿元" in result
    assert "商誉金额：34.41亿元" in result
    assert "evidence handle" not in result
    assert "candidate evidence" not in result


def test_final_answer_strips_leading_research_worklog_without_touching_body() -> None:
    text = (
        "找到了2024年度财报，现在直接读取原文相关chunk。\n"
        "原文已取得。关键数据在chunk `ev_private_12345678`中，现在进行计算验证。\n"
        "以下是引用年度报告原文的完整答案：\n\n---\n\n"
        "2024年营业收入为1,741.44亿元。"
    )

    assert _strip_leading_assistant_progress(text) == "2024年营业收入为1,741.44亿元。"


def test_final_answer_strips_collected_data_progress_leadin() -> None:
    text = (
        "现已收集到足够数据。以下是综合整理的结果：\n\n"
        "---\n\n"
        "## 存储产品与涨价幅度\n\n"
        "| 公司 | 核心产品 |\n|---|---|\n| 美光 | DRAM、NAND |"
    )

    assert _strip_leading_assistant_progress(text).startswith("## 存储产品与涨价幅度")


def test_final_answer_strips_english_ready_to_compile_progress() -> None:
    text = (
        "I now have all the data needed. Let me compile the comparison.\n\n"
        "| Company | Revenue |\n|---|---|\n| Example | 100 |"
    )

    assert _strip_leading_assistant_progress(text).startswith("| Company | Revenue |")


def test_progress_strip_preserves_user_facing_source_introduction() -> None:
    text = "以下数据来源于年度报告。\n\n营业收入为1,741.44亿元。"

    assert _strip_leading_assistant_progress(text) == text


def test_strict_list_strips_completed_reading_worklog_and_source_leadin() -> None:
    text = (
        "已完整阅读电话会原文。现在整理结果：\n\n---\n\n"
        "根据微软 FY2026 Q1 电话会（2025年10月29日）原文：\n\n"
        "**Azure 增长率**：40% [1](evidence://ev_azure_12345678)\n\n"
        "**AI 服务贡献百分点**：原文未披露。"
    )

    without_progress = _strip_leading_assistant_progress(text)
    assert without_progress.startswith("根据微软 FY2026 Q1")
    assert _strip_strict_scope_leadin(
        without_progress,
        "请仅列出 Azure 增长率和 AI 服务贡献百分点。",
    ).startswith("**Azure 增长率**")


def test_strict_chinese_list_drops_english_provisional_duplicate() -> None:
    text = (
        "I now have the full transcript. I found the key Azure data. "
        "Let me identify the specific figures:\n\n"
        "1. Azure growth was 40%. [1](evidence://ev_azure_12345678)\n\n"
        "2. AI contribution was not disclosed. "
        "[2](evidence://ev_ai_12345678)\n\n"
        "根据微软 FY2026 Q1 电话会原文：\n\n"
        "**Azure 收入增长率**：40% [1](evidence://ev_azure_12345678)\n\n"
        "**AI 服务贡献百分点**：未披露 [2](evidence://ev_ai_12345678)"
    )

    result = _strip_strict_scope_leadin(
        text,
        "请根据微软电话会，仅列出 Azure 增长率和 AI 服务贡献百分点。",
    )

    assert result.startswith("**Azure 收入增长率**")
    assert "I now have" not in result
    assert result.count("Azure 收入增长率") == 1


def test_strict_list_drops_full_document_leadin_and_trailing_recap() -> None:
    text = (
        "在完整逐页读取了微软 FY2026 Q1 电话会原文（共 104 个文本块，已全部检索）后，"
        "确认以下情况：\n\n"
        "**Azure 增长率**：40% [1](evidence://ev_azure_12345678)\n\n"
        "**AI 服务贡献百分点**：未披露 [2](evidence://ev_ai_12345678)\n\n"
        "**结论**：以上为完整原文检索结果。"
    )

    result = _strip_strict_scope_leadin(
        text,
        "请仅列出 Azure 增长率和 AI 服务贡献百分点。",
    )

    assert result.startswith("**Azure 增长率**")
    assert "完整逐页读取" not in result
    assert "结论" not in result


def test_strict_list_drops_source_leadin_with_trailing_date() -> None:
    text = (
        "根据微软 FY2026 Q1 电话会原文（2025年10月29日）：\n\n"
        "**Azure 增长率**：40% [1](evidence://ev_azure_12345678)\n\n"
        "**AI 服务贡献百分点**：未披露 [2](evidence://ev_ai_12345678)"
    )

    result = _strip_strict_scope_leadin(
        text,
        "请仅列出 Azure 增长率和 AI 服务贡献百分点。",
    )

    assert result.startswith("**Azure 增长率**")
    assert "2025年10月29日" not in result


def test_strict_list_keeps_only_rows_after_provisional_sourced_recap() -> None:
    text = (
        "已读完电话会全文。以下是核实结果：\n\n"
        "电话会原文明确披露了 Azure 增长率 40%，但未披露 AI 服务贡献百分点 "
        "[1](evidence://ev_azure_12345678)：\n\n"
        "“Azure AI services revenue was generally in line with expectations.” "
        "[2](evidence://ev_ai_12345678)\n\n"
        "原文没有披露 AI 服务贡献 Azure 增长的具体百分点数字。\n\n"
        "根据微软 FY2026 Q1 电话会（2025 年 10 月 29 日）原文：\n\n"
        "**Azure 增长率**：40% [1](evidence://ev_azure_12345678)\n\n"
        "**AI 服务贡献百分点**：未披露 [2](evidence://ev_ai_12345678)"
    )

    result = _strip_strict_scope_leadin(
        text,
        "请仅列出 Azure 增长率和 AI 服务贡献百分点。",
    )

    assert result.startswith("**Azure 增长率**")
    assert "已读完" not in result
    assert result.count("Azure 增长率") == 1
    assert result.count("AI 服务贡献百分点") == 1


def test_strict_list_drops_unrequested_follow_up_advice() -> None:
    text = (
        "**Azure 增长率**：40% [1](evidence://ev_azure_12345678)\n\n"
        "**AI 服务贡献百分点**：原文未披露。 "
        "[2](evidence://ev_ai_12345678)\n\n"
        "如需该数据，建议查阅其他演示幻灯片。"
    )

    result = _strip_strict_scope_leadin(
        text,
        "请仅列出 Azure 增长率和 AI 服务贡献百分点。",
    )

    assert result.endswith("[2](evidence://ev_ai_12345678)")
    assert "建议" not in result


def test_strict_two_line_request_drops_trailing_source_inventory() -> None:
    text = (
        "直销渠道：收入 748.43 亿元 [1](evidence://ev_direct_12345678)\n"
        "批发代理：收入 957.69 亿元 [2](evidence://ev_wholesale_12345678)\n\n"
        "数据来源：贵州茅台 2024 年年度报告、渠道经营表。"
    )

    result = _strip_strict_scope_leadin(
        text,
        "请根据贵州茅台2024年年度报告，只用两行列出直销渠道和批发代理渠道。",
    )

    assert result.count("\n") == 1
    assert "  \n" in result
    assert "数据来源" not in result


def test_strict_two_line_request_flattens_table_and_drops_source_restatement() -> None:
    text = (
        "| 渠道 | 本期销售收入（元） | 本期销售收入（亿元） | 同比增幅 |\n"
        "|---|---:|---:|---:|\n"
        "| 直销 | 74,843,327,030.79 [1](evidence://ev_direct_12345678) | "
        "748.43 [1](evidence://ev_direct_12345678) | +11.32% |\n"
        "| 批发代理 | 95,768,511,021.23 [2](evidence://ev_wholesale_12345678) | "
        "957.69 [2](evidence://ev_wholesale_12345678) | +19.73% |\n\n"
        "以上数据均直接引用自年度报告主营业务分销售模式情况表。"
    )

    result = _strip_strict_scope_leadin(
        text,
        "请根据年度报告，只用两行列出直销渠道和批发代理渠道数据，并逐行引用原文。",
    )

    assert result.count("\n") == 1
    assert "  \n" in result
    assert result.startswith("直销；本期销售收入（元）：74,843,327,030.79")
    assert result.splitlines()[1].startswith("批发代理；")
    assert "以上数据" not in result
    assert "ev_direct_12345678" in result
    assert "ev_wholesale_12345678" in result


def test_strict_markdown_table_request_keeps_only_the_table() -> None:
    text = (
        "数据已齐全，下面给出对比结果。\n\n"
        "| 公司 | 营业收入 |\n"
        "|---|---:|\n"
        "| 闪迪 | 72 亿美元 [1](evidence://ev_sandisk_12345678) |\n"
        "| 美光 | 374 亿美元 [2](evidence://ev_micron_12345678) |\n\n"
        "补充说明：两家公司口径不同。"
    )

    result = _strip_strict_scope_leadin(
        text,
        "请只输出 Markdown 表格，不要扩展分析。",
    )

    assert result.startswith("| 公司 | 营业收入 |")
    assert result.endswith("[2](evidence://ev_micron_12345678) |")
    assert "数据已齐全" not in result
    assert "补充说明" not in result


def test_explicit_no_retrieval_request_skips_zero_evidence_repair() -> None:
    _store, _live, observer = _observer_with_citations()
    observer._user_prompt = "用通俗语言解释 ROE，不需要查询具体公司数据。"
    bundle = {
        "citations": [],
        "quality": {
            "claims": [
                {
                    "citationRequired": True,
                    "issueCodes": ["claim_without_citation"],
                }
            ],
            "metrics": {"unsourcedClaimCount": 1, "unverifiedClaimCount": 0},
        },
    }

    assert (
        observer._citation_repair_skip_reason(bundle, "ROE 是净资产收益率。")
        == "user-requested-no-retrieval"
    )


def test_unrequested_retrieval_internals_are_removed_from_cited_answer() -> None:
    text = (
        "**AI 服务贡献百分点**：原文未披露具体数字。\n"
        "电话会中的 excerpt 被截断，全文 104 个 chunk 均未出现该数字 "
        "[2](evidence://ev_ai_12345678)。"
        "电话会原文未就此项给出量化披露 "
        "[2](evidence://ev_ai_12345678)。"
    )

    result = _strip_unrequested_retrieval_internals(text, "请仅列出该数字。")

    assert "excerpt" not in result
    assert "chunk" not in result
    assert result.startswith("**AI 服务贡献百分点**")
    assert result.endswith("[2](evidence://ev_ai_12345678)。")


def test_retrieval_internals_are_kept_for_explicit_technical_question() -> None:
    text = "该 excerpt 对应 104 个 chunks。"
    assert (
        _strip_unrequested_retrieval_internals(
            text,
            "请解释 excerpt 和 chunks 的关系。",
        )
        == text
    )


def test_retrieval_scan_absence_is_rewritten_as_user_facing_disclosure() -> None:
    text = (
        "经过完整扫描全部104个chunks，文稿中未找到 Amy Hood 明确报出的 AI 服务"
        "对 Azure 增长的具体贡献百分点数字。\n\n"
        "需要说明：文稿提供方对部分句子有节选截断，建议直接查阅原始 PDF。"
    )

    result = _strip_unrequested_retrieval_internals(text, "请列出该指标。")

    assert result == ("原文未披露Amy Hood 明确报出的 AI 服务对 Azure 增长的具体贡献百分点数字。")
    assert "chunk" not in result
    assert "截断" not in result


def test_incomplete_content_wording_becomes_clean_cited_non_disclosure() -> None:
    text = (
        "**AI 服务贡献百分点**：电话会原文在提及 Azure AI services 后内容"
        "未完整披露具体贡献百分点数字 [2](evidence://ev_ai_12345678)。"
        "检索了该电话会全部内容，原文中未找到明确百分点数字披露。"
    )

    result = _strip_unrequested_retrieval_internals(text, "请列出该指标。")

    assert result == ("**AI 服务贡献百分点**：原文未披露具体数字 [2](evidence://ev_ai_12345678)。")
    assert "检索" not in result
    assert "未完整" not in result


def test_labeled_internal_absence_keeps_field_and_citation() -> None:
    text = (
        "**AI 服务贡献百分点**：全文检索后仍未找到具体数字，可能位于截断内容中 "
        "[2](evidence://ev_ai_12345678)。\n\n"
        "检索了全部内容，建议用户另行查找。"
    )

    result = _strip_unrequested_retrieval_internals(text, "请列出该指标。")

    assert result == ("**AI 服务贡献百分点**：原文未披露具体数字 [2](evidence://ev_ai_12345678)。")
    assert "AI 服务贡献百分点" in result


def test_table_internal_absence_keeps_row_and_citation() -> None:
    text = (
        "| 指标 | 数值 |\n"
        "|------|------|\n"
        "| Azure 同比增长率 | **40%** [1](evidence://ev_azure_12345678) |\n"
        "| AI 服务贡献百分点 | **原文未能完整获取**——电话会文本在引文处截断，"
        "返回的全部 104 个文本段落中均未出现具体数字。无法引用具体数值 "
        "[2](evidence://ev_ai_12345678)。 |"
    )

    result = _strip_unrequested_retrieval_internals(text, "请仅列出两个指标。")

    assert "| AI 服务贡献百分点 | 原文未披露具体数字 " in result
    assert "[2](evidence://ev_ai_12345678)" in result
    assert "截断" not in result
    assert "文本段落" not in result
    assert "无法引用" not in result


def test_bold_labeled_multisentence_absence_keeps_citation() -> None:
    text = (
        '**AI 服务贡献百分点：**未找到。电话会原文在 Amy Hood 提及"Azure AI '
        'services revenue was generall…"处截断，整份逐字记录读取完毕（共104段）后，'
        "均未出现具体百分点，该数字未能确认 "
        "[2](evidence://ev_ai_12345678)。"
    )

    result = _strip_unrequested_retrieval_internals(text, "请仅列出该指标。")

    assert result == ("**AI 服务贡献百分点：**原文未披露具体数字 [2](evidence://ev_ai_12345678)。")


def test_labeled_absence_prefers_full_document_citation_over_duplicate_excerpt() -> None:
    text = (
        "**AI 服务贡献百分点：**原文未披露 "
        "[2](evidence://ev_doc_coverage_12345678)。电话会中相关句子不完整，"
        "整份记录中未出现具体百分点 "
        "[3](evidence://ev_text_12345678)。"
    )

    result = _strip_unrequested_retrieval_internals(text, "请仅列出该指标。")

    assert result == (
        "**AI 服务贡献百分点：**原文未披露具体数字 [2](evidence://ev_doc_coverage_12345678)。"
    )
    assert "ev_text_12345678" not in result


def test_strict_labeled_absence_collapses_duplicate_same_citation() -> None:
    text = (
        "**AI 服务贡献百分点：**原始电话会文本未披露具体数字 "
        "[2](evidence://ev_doc_coverage_12345678)。电话会中未找到明确百分点，"
        "原文未作数字化披露 [2](evidence://ev_doc_coverage_12345678)。"
    )

    result = _strip_unrequested_retrieval_internals(text, "请仅列出该指标。")

    assert result == (
        "**AI 服务贡献百分点：**原文未披露具体数字 [2](evidence://ev_doc_coverage_12345678)。"
    )
    assert result.count("evidence://") == 1


def test_internal_negative_sentence_is_rewritten_instead_of_leaving_bare_citation() -> None:
    text = (
        "全文扫描后未发现具体百分点，索引文本内容不完整 [2](evidence://ev_doc_coverage_12345678)。"
    )

    result = _strip_unrequested_retrieval_internals(text, "请仅列出该指标。")

    assert result == ("原文未披露具体数字 [2](evidence://ev_doc_coverage_12345678)。")


def test_strict_output_never_publishes_a_bare_citation_block() -> None:
    text = "结果如下。\n\n[2](evidence://ev_doc_coverage_12345678)"

    result = _strip_unrequested_retrieval_internals(text, "请仅列出该指标。")

    assert "\n\n原文未披露具体数字 " in result
    assert "\n\n[2](" not in result


def test_progress_only_internal_retrieval_block_is_suppressed() -> None:
    text = (
        "I have discovery results but no direct `evidenceHandle` values on specific claims. "
        "Let me fetch the actual documents to get proper handles."
    )

    assert _strip_leading_assistant_progress(text) == ""


def test_strict_list_request_drops_unrequested_source_leadin() -> None:
    text = (
        "以下为贵州茅台2024年年度报告披露的三项信息：\n\n"
        "**披露日期：** 2025年4月2日 [1](evidence://ev_date_12345678)\n\n"
        "**审计意见：** 标准无保留意见 [2](evidence://ev_audit_12345678)"
    )

    assert _strip_strict_scope_leadin(
        text,
        "请根据年度报告，只列出披露日期和审计意见。",
    ).startswith("**披露日期：**")


def test_strict_list_request_drops_english_research_completion_note() -> None:
    text = (
        "Note: the annual report directly discloses the YoY growth rates in the table, "
        "so the calculated values match what the report shows. "
        "Now I have everything needed.\n\n"
        "**按产品划分**\n\n"
        "| 产品 | 收入 |\n| --- | --- |\n"
        "| 茅台酒 | 1,459.28 [1](evidence://ev_product_12345678) |"
    )

    assert _strip_strict_scope_leadin(
        text,
        "请仅列出茅台酒收入，不要增加其他内容。",
    ).startswith("**按产品划分**")


def test_strict_list_request_drops_claimed_verification_leadin() -> None:
    text = (
        "所有数据均已核实，以下是来自贵州茅台 2024 年年报的数据：\n\n"
        "**按产品划分**\n\n"
        "| 产品 | 收入 |\n| --- | --- |\n"
        "| 茅台酒 | 1,459.28 [1](evidence://ev_product_12345678) |"
    )

    assert _strip_strict_scope_leadin(
        text,
        "请仅列出茅台酒收入，不要增加其他内容。",
    ).startswith("**按产品划分**")


def test_citation_request_keeps_concise_restatement_instead_of_duplicate_excerpt() -> None:
    text = (
        "据年度报告披露 [1](evidence://ev_intro_12345678)：\n\n"
        "> 归母净利润862.28亿元，同比增长15.38% "
        "[2](evidence://ev_quote_12345678)\n\n"
        "即："
        "- 归母净利润862.28亿元，同比增长15.38% "
        "[2](evidence://ev_quote_12345678)"
    )

    assert _strip_unrequested_source_excerpt(
        text,
        "净利润是多少？同比增长多少？请引用年度报告原文。",
    ) == ("- 归母净利润862.28亿元，同比增长15.38% [2](evidence://ev_quote_12345678)")


def test_explicit_verbatim_request_preserves_source_excerpt() -> None:
    text = (
        "回答 [1](evidence://ev_intro_12345678)\n\n"
        "年报原文：\n\n> 逐字内容 [1](evidence://ev_intro_12345678)"
    )

    assert (
        _strip_unrequested_source_excerpt(
            text,
            "请逐字引用并给出年度报告原文段落。",
        )
        == text
    )


def test_progress_strip_removes_reverse_order_retrieval_summary() -> None:
    text = (
        '年报中有两处渠道数据披露，单位不同。"主营业务分销售模式情况"表和'
        '"公司收入及成本分析"渠道表均已找到，数据一致，现引用元口径：\n\n'
        "直销收入 74 元 [1](evidence://ev_rpt_first_12345678)\n\n"
        "批发代理收入 95 元 [1](evidence://ev_rpt_first_12345678)"
    )

    assert _strip_leading_assistant_progress(text) == (
        "直销收入 74 元 [1](evidence://ev_rpt_first_12345678)\n\n"
        "批发代理收入 95 元 [1](evidence://ev_rpt_first_12345678)"
    )


def test_progress_strip_removes_pending_retrieval_plan() -> None:
    text = (
        "需要从2024年报原文中获取按销售模式（直销/批发代理）的精确数据。\n\n"
        "直销渠道：748.43亿元 [1](evidence://ev_direct_12345678)\n\n"
        "批发代理渠道：957.69亿元 [2](evidence://ev_wholesale_12345678)"
    )

    assert _strip_leading_assistant_progress(text) == (
        "直销渠道：748.43亿元 [1](evidence://ev_direct_12345678)\n\n"
        "批发代理渠道：957.69亿元 [2](evidence://ev_wholesale_12345678)"
    )


def test_progress_strip_removes_provisional_recap_before_final_answer() -> None:
    text = (
        "现在查找年度报告的正式披露日期。由于文档发布时间是2025-04-02，这通常出现在最后几页。\n\n"
        "基于已收集的信息，现在可以给出完整答案：\n\n"
        "- 披露日期：已有证据。\n- 审计意见：已有证据。\n- 营业收入：已有证据。\n\n"
        "---\n\n"
        "以下为根据年度报告披露的三项信息：\n\n"
        "**披露日期：** 2025年4月2日 [1](evidence://ev_date_12345678)\n\n"
        "**审计意见：** 标准无保留意见 [2](evidence://ev_audit_12345678)"
    )

    assert _strip_leading_assistant_progress(text) == (
        "**披露日期：** 2025年4月2日 [1](evidence://ev_date_12345678)\n\n"
        "**审计意见：** 标准无保留意见 [2](evidence://ev_audit_12345678)"
    )


def test_progress_strip_keeps_legitimate_chunk_evidence_links() -> None:
    text = (
        "### FY2026 Q1\n\n"
        "需求持续扩散 [source](evidence://ev_chunk_dd4b0c9cf48267a07aa7c828)。\n\n"
        "### FY2026 Q2\n\n"
        "新增近 1 GW [source](evidence://ev_chunk_2e41783debebe3ab994f79bb)。"
    )

    assert _strip_leading_assistant_progress(text) == text


def test_retrieval_cleanup_does_not_match_chunk_inside_evidence_handle() -> None:
    text = (
        "需求持续扩散 [source](evidence://ev_chunk_dd4b0c9cf48267a07aa7c828)。\n\n"
        "本次检索块没有提供资本开支绝对值。"
    )

    assert _strip_unrequested_retrieval_internals(
        text,
        "请总结电话会。",
    ) == (
        "需求持续扩散 [source](evidence://ev_chunk_dd4b0c9cf48267a07aa7c828)。\n\n"
        "原文未披露具体数字。"
    )


def test_retrieval_cleanup_hides_incomplete_transcript_transport_wording() -> None:
    text = (
        "*本季度电话会检索到的原文未涉及具体季度资本开支金额，"
        "Amy Hood 的财务指引部分未完整收录。*"
    )

    assert _strip_unrequested_retrieval_internals(
        text,
        "请按季度总结资本开支。",
    ) == "当前来源未包含具体数字。"


def test_progress_strip_removes_doc_id_and_numeric_chunk_worklog() -> None:
    text = (
        "年报 doc_id 已确认。现在直接 fetch 年报中的渠道数据原文段落：\n"
        "年报原文已完整获取，渠道数据在 chunk `661357847958757` 中有明确记载。\n\n"
        "直销渠道：748.43亿元 [1](evidence://ev_direct_12345678)\n\n"
        "批发代理渠道：957.69亿元 [2](evidence://ev_wholesale_12345678)"
    )

    assert _strip_leading_assistant_progress(text) == (
        "直销渠道：748.43亿元 [1](evidence://ev_direct_12345678)\n\n"
        "批发代理渠道：957.69亿元 [2](evidence://ev_wholesale_12345678)"
    )


def test_progress_strip_removes_multiline_handle_notes_before_cited_answer() -> None:
    text = (
        "年报中有两处渠道数据披露，单位不同：\n"
        "1. 主营业务分销售模式—`ev_rpt_first_12345678`：批发代理 95 元\n"
        "2. 渠道表—`ev_rpt_second_12345678`：直销 74 元\n\n"
        "用户要求单位为元，evidence handle已确认。\n\n"
        "直销营业收入 74 元 [年报](evidence://ev_rpt_first_12345678)\n\n"
        "批发代理营业收入 95 元 [年报](evidence://ev_rpt_first_12345678)"
    )

    assert _strip_leading_assistant_progress(text) == (
        "直销营业收入 74 元 [年报](evidence://ev_rpt_first_12345678)\n\n"
        "批发代理营业收入 95 元 [年报](evidence://ev_rpt_first_12345678)"
    )


def test_progress_strip_removes_innocent_preamble_around_internal_handle_list() -> None:
    text = (
        "年报原文中已有两处披露渠道收入：\n\n"
        "1. 主营业务分销售模式表—`ev_rpt_first_12345678`\n"
        "2. 公司收入及成本分析渠道表—`ev_rpt_second_12345678`\n\n"
        "以元为单位的精确数据来自主营业务分销售模式表，以下直接呈现：\n\n"
        "---\n\n"
        "直销收入 74 元 [1](evidence://ev_rpt_first_12345678)\n\n"
        "批发代理收入 95 元 [2](evidence://ev_rpt_first_12345678)"
    )

    assert _strip_leading_assistant_progress(text) == (
        "直销收入 74 元 [1](evidence://ev_rpt_first_12345678)\n\n"
        "批发代理收入 95 元 [2](evidence://ev_rpt_first_12345678)"
    )


def _observer() -> tuple[_FakeStore, _RecordingSink, _MessageObserverSink]:
    store = _FakeStore()
    live = _RecordingSink()
    db = DatabaseEventSink(store, "owner-1", "sess-1", "msg-1")
    persist_then_live = PersistThenBroadcastSink(db, live)
    coalesced = DeltaCoalescingSink(persist_then_live)
    return store, live, _MessageObserverSink(coalesced)


def _observer_with_citations(
    *,
    verification_enabled: bool = True,
) -> tuple[_FakeStore, _RecordingSink, _MessageObserverSink]:
    store = _FakeStore()
    live = _RecordingSink()
    db = DatabaseEventSink(store, "owner-1", "sess-1", "msg-1")
    persist_then_live = PersistThenBroadcastSink(db, live)
    coalesced = DeltaCoalescingSink(persist_then_live)
    observer = _MessageObserverSink(
        coalesced,
        message_id="msg-1",
        user_prompt="根据文档回答并引用",
        citation_policy_available=True,
        citation_verification_enabled=verification_enabled,
    )
    return store, live, observer


def _observer_with_strict_policy() -> tuple[_FakeStore, _RecordingSink, _MessageObserverSink]:
    store = _FakeStore()
    live = _RecordingSink()
    db = DatabaseEventSink(store, "owner-1", "sess-1", "msg-1")
    persist_then_live = PersistThenBroadcastSink(db, live)
    observer = _MessageObserverSink(
        DeltaCoalescingSink(persist_then_live),
        message_id="msg-1",
        user_prompt="请核验财务数据",
        citation_policy_available=True,
        citation_quality_policy={
            "policy_id": "strict-test",
            "revision": "strict-test-v1",
            "mode": "strict-domain",
            "config": {
                "rules": {
                    "factual_claim": {"citation_required": True},
                    "numeric_claim": {
                        "require_unit": True,
                        "require_period_or_as_of": True,
                        "require_value_in_answer": True,
                    },
                },
                "failure": {"publish_on_degraded": "draft_only"},
            },
        },
    )
    return store, live, observer


def _claim_patch_json(
    observer: _MessageObserverSink,
    *,
    replacement_text: str,
    evidence_handles: list[str],
) -> str:
    context = json.loads(
        observer.citation_repair_prompt.split("Restricted repair context (JSON):\n", 1)[1]
    )
    return json.dumps(
        {
            "version": "citation-claim-patch-v1",
            "patches": [
                {
                    "claimId": context["claimIssues"][0]["claimId"],
                    "replacementText": replacement_text,
                    "evidenceHandles": evidence_handles,
                }
            ],
        },
        ensure_ascii=False,
    )


async def test_interrupted_turn_persists_partial_assistant_text_before_idle() -> None:
    store, live, observer = _observer()

    await observer.emit(Event(type="text_delta", data={"text": "already "}))
    await observer.emit(Event(type="text_delta", data={"text": "streamed"}))
    await observer.emit(
        Event(
            type="session_idle",
            data={
                "stop_reason": {"type": "error", "category": "user_interrupt"},
                "num_turns": 1,
            },
        )
    )

    assert [event.type for event in store.appended] == ["assistant_message", "session_idle"]
    assert store.appended[0].data == {"text": "already streamed"}
    assert observer.assistant_text == "already streamed"

    assert [event.type for event in live.events] == [
        "text_delta",
        "assistant_message",
        "session_idle",
    ]
    assert "seq" not in live.events[0].data
    assert live.events[1].data["seq"] == 101
    assert live.events[2].data["seq"] == 102


async def test_final_assistant_message_wins_over_streamed_delta() -> None:
    store, live, observer = _observer()

    await observer.emit(Event(type="text_delta", data={"text": "draft"}))
    await observer.emit(Event(type="assistant_message", data={"text": "final"}))
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))

    assert [event.type for event in store.appended] == ["assistant_message", "session_idle"]
    assert store.appended[0].data == {"text": "final"}
    assert observer.assistant_text == "final"
    assert [event.type for event in live.events] == [
        "text_delta",
        "assistant_message",
        "session_idle",
    ]


async def test_citation_turn_preserves_complete_stream_when_canonical_is_only_epilogue() -> None:
    store, _live, observer = _observer_with_citations()
    evidence = {
        "_valuz_evidence": {
            "evidenceHandle": "ev_stream_complete_2026",
            "source": {
                "sourceId": "transcript-msft-q4",
                "providerId": "reportify",
                "documentId": "transcript-msft-q4",
                "sourceType": "document",
                "title": "Microsoft FY2026 Q4 earnings call",
                "retrievedAt": "2026-08-01T10:00:00Z",
            },
            "evidence": {
                "kind": "text",
                "quote": "Demand continued to exceed available supply.",
                "snippet": "Demand continued to exceed available supply.",
                "capturedAt": "2026-08-01T10:00:00Z",
            },
            "locator": {"kind": "chunk", "chunkId": "chunk-q4"},
        }
    }
    await observer.emit(Event(type="tool_use", data={"id": "tool-1", "name": "search"}))
    await observer.emit(
        Event(type="tool_result", data={"id": "tool-1", "content": json.dumps(evidence)})
    )
    epilogue = "以上是对应季度的原文表述。"
    full_answer = (
        "## FY2026 Q4\n\n"
        "管理层表示 Demand continued to exceed available supply. "
        "[1](evidence://ev_stream_complete_2026)。\n\n"
        + ("补充说明保持在同一季度原文范围内。" * 20)
        + "\n\n"
        + epilogue
    )
    await observer.emit(Event(type="text_delta", data={"text": full_answer}))
    await observer.emit(Event(type="assistant_message", data={"text": epilogue}))
    await observer.emit(
        Event(
            type="session_idle",
            data={
                "num_turns": 1,
                "stop_reason": {"type": "error", "category": "user_interrupt"},
            },
        )
    )

    assistant = next(event for event in store.appended if event.type == "assistant_message")
    assert "FY2026 Q4" in assistant.data["text"]
    assert "citation://cit_" in assistant.data["text"]
    assert len(assistant.data["text"]) > len(epilogue) * 2


async def test_internal_compaction_handoff_is_not_persisted_or_broadcast() -> None:
    store, live, observer = _observer()
    handoff = """## SESSION INTENT
Research the filing.

## SUMMARY
Internal state.

## ARTIFACTS
None.

## NEXT STEPS
Continue with tools.
"""

    await observer.emit(Event(type="assistant_message", data={"text": handoff}))
    await observer.emit(Event(type="tool_use", data={"id": "tool-1", "name": "search"}))
    await observer.emit(Event(type="assistant_message", data={"text": "Visible answer."}))
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))

    assistants = [event for event in store.appended if event.type == "assistant_message"]
    assert [event.data["text"] for event in assistants] == ["Visible answer."]
    assert "SESSION INTENT" not in observer.assistant_text
    assert all("SESSION INTENT" not in str(event.data.get("text") or "") for event in live.events)


async def test_tool_call_preamble_is_not_part_of_the_final_answer() -> None:
    store, live, observer = _observer_with_citations()
    evidence = {
        "_valuz_evidence": {
            "evidenceHandle": "ev_revenue_2025",
            "source": {
                "sourceId": "doc-1",
                "providerId": "docs",
                "documentId": "doc-1",
                "sourceType": "document",
                "title": "Report",
                "retrievedAt": "2026-07-30T10:00:00Z",
            },
            "evidence": {
                "kind": "text",
                "quote": "Revenue increased by 20%.",
                "snippet": "Revenue increased by 20%.",
                "capturedAt": "2026-07-30T10:00:00Z",
            },
            "locator": {"kind": "chunk", "chunkId": "chunk-1"},
        }
    }
    await observer.emit(
        Event(
            type="assistant_message",
            data={
                "text": (
                    "根据记忆先给出：Revenue increased by 19% "
                    "[1](evidence://ev_fake_12345678)。现在查原文。"
                )
            },
        )
    )
    await observer.emit(Event(type="tool_use", data={"id": "tool-1", "name": "docs"}))
    await observer.emit(
        Event(type="tool_result", data={"id": "tool-1", "content": json.dumps(evidence)})
    )
    await observer.emit(
        Event(
            type="assistant_message",
            data={"text": "Revenue increased by 20% [1](evidence://ev_revenue_2025)."},
        )
    )
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))

    assistants = [event for event in store.appended if event.type == "assistant_message"]
    assert len(assistants) == 1
    assert "19%" not in assistants[0].data["text"]
    assert "ev_fake" not in observer.assistant_text
    assert "20%" in observer.assistant_text
    assert any(event.type == "tool_use" for event in live.events)


async def test_citation_candidate_followed_by_new_text_is_never_published() -> None:
    store, _live, observer = _observer_with_citations()
    await observer.emit(
        Event(
            type="assistant_message",
            data={"text": ("Draft with raw protocol [1](evidence://ev_fake_12345678).")},
        )
    )
    await observer.emit(Event(type="text_delta", data={"text": "Final answer."}))
    await observer.emit(Event(type="assistant_message", data={"text": "Final answer."}))
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))

    assistants = [event for event in store.appended if event.type == "assistant_message"]
    assert [event.data["text"] for event in assistants] == ["Final answer."]
    assert "ev_fake" not in observer.assistant_text


async def test_final_assistant_message_captures_citation_bundle() -> None:
    _store, _live, observer = _observer()

    await observer.emit(
        Event(
            type="assistant_message",
            data={"text": "final"},
        )
    )
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))

    assert observer.citation_bundle is None


async def test_final_assistant_is_guarded_before_persistence_and_broadcast() -> None:
    store, live, observer = _observer_with_citations()
    evidence = {
        "_valuz_evidence": {
            "evidenceHandle": "ev_revenue_2025",
            "source": {
                "sourceId": "doc-1",
                "providerId": "docs",
                "documentId": "doc-1",
                "sourceType": "document",
                "title": "Report",
                "retrievedAt": "2026-07-30T10:00:00Z",
            },
            "evidence": {
                "kind": "text",
                "quote": "Revenue increased.",
                "snippet": "Revenue increased.",
                "capturedAt": "2026-07-30T10:00:00Z",
            },
            "locator": {"kind": "pdf", "page": 1},
        }
    }
    await observer.emit(Event(type="tool_use", data={"id": "tool-1", "name": "doc_search"}))
    await observer.emit(
        Event(type="tool_result", data={"id": "tool-1", "content": json.dumps(evidence)})
    )
    await observer.emit(
        Event(
            type="assistant_message",
            data={"text": "Claim [report](evidence://ev_revenue_2025)."},
        )
    )
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))

    assistant = next(event for event in store.appended if event.type == "assistant_message")
    assert "evidence://" not in assistant.data["text"]
    assert "citation://cit_" in assistant.data["text"]
    assert assistant.data["citation_bundle"]["integrity"]["status"] == "passed"
    assert observer.citation_bundle == assistant.data["citation_bundle"]
    live_assistant = next(event for event in live.events if event.type == "assistant_message")
    assert live_assistant.data["citation_bundle"] == assistant.data["citation_bundle"]


async def test_unrelated_oversized_tool_result_does_not_retry_clean_cited_answer() -> None:
    store, _live, observer = _observer_with_citations()
    evidence = {
        "_valuz_evidence": {
            "evidenceHandle": "ev_revenue_2025",
            "source": {
                "sourceId": "doc-1",
                "providerId": "docs",
                "documentId": "doc-1",
                "sourceType": "document",
                "title": "Report",
                "retrievedAt": "2026-07-30T10:00:00Z",
            },
            "evidence": {
                "kind": "text",
                "quote": "Revenue increased.",
                "snippet": "Revenue increased.",
                "capturedAt": "2026-07-30T10:00:00Z",
            },
            "locator": {"kind": "pdf", "page": 1},
        }
    }
    await observer.emit(Event(type="tool_use", data={"id": "tool-1", "name": "docs"}))
    await observer.emit(
        Event(type="tool_result", data={"id": "tool-1", "content": json.dumps(evidence)})
    )
    await observer.emit(Event(type="tool_use", data={"id": "tool-2", "name": "other"}))
    await observer.emit(
        Event(
            type="tool_result",
            data={
                "id": "tool-2",
                "content": '{"_valuz_evidence":' + ("x" * 2_000_100),
            },
        )
    )
    await observer.emit(
        Event(
            type="assistant_message",
            data={"text": "Revenue increased [source](evidence://ev_revenue_2025)."},
        )
    )
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))

    assert observer.citation_repair_requested is False
    assistant = next(event for event in store.appended if event.type == "assistant_message")
    assert assistant.data["citation_bundle"]["quality"]["status"] == "passed"
    assert assistant.data["citation_bundle"]["integrity"]["evidenceOverflowReasons"]


async def test_private_citation_content_is_registered_but_not_forwarded() -> None:
    store, live, observer = _observer_with_citations()
    evidence = {
        "_valuz_evidence": {
            "evidenceHandle": "ev_persisted_2025",
            "source": {
                "sourceId": "doc-1",
                "providerId": "docs",
                "documentId": "doc-1",
                "sourceType": "document",
                "title": "Report",
                "retrievedAt": "2026-07-30T10:00:00Z",
            },
            "evidence": {
                "kind": "text",
                "quote": "Revenue increased.",
                "snippet": "Revenue increased.",
                "capturedAt": "2026-07-30T10:00:00Z",
            },
            "locator": {"kind": "pdf", "page": 1},
        }
    }
    await observer.emit(Event(type="tool_use", data={"id": "tool-1", "name": "doc_search"}))
    await observer.emit(
        Event(
            type="tool_result",
            data={
                "id": "tool-1",
                "content": "<persisted-output>placeholder</persisted-output>",
                "_citation_content": json.dumps(evidence),
            },
        )
    )
    await observer.emit(
        Event(
            type="assistant_message",
            data={"text": "Claim [report](evidence://ev_persisted_2025)."},
        )
    )
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))

    tool_result = next(event for event in store.appended if event.type == "tool_result")
    assert "_citation_content" not in tool_result.data
    assert (
        "_citation_content"
        not in next(event for event in live.events if event.type == "tool_result").data
    )
    assistant = next(event for event in store.appended if event.type == "assistant_message")
    assert "citation://cit_" in assistant.data["text"]
    assert len(assistant.data["citation_bundle"]["citations"]) == 1


async def test_large_private_citation_content_registers_evidence_without_forwarding() -> None:
    store, live, observer = _observer_with_citations()
    evidence = {
        "padding": "x" * 2_100_000,
        "_valuz_evidence": {
            "evidenceHandle": "ev_large_persisted_2025",
            "source": {
                "sourceId": "transcript-1",
                "providerId": "search",
                "documentId": "transcript-1",
                "sourceType": "document",
                "title": "Earnings call transcript",
                "retrievedAt": "2026-07-30T10:00:00Z",
            },
            "evidence": {
                "kind": "text",
                "quote": "Cloud revenue increased by 20%.",
                "snippet": "Cloud revenue increased by 20%.",
                "capturedAt": "2026-07-30T10:00:00Z",
            },
            "locator": {"kind": "chunk", "chunkId": "chunk-1"},
        },
    }
    await observer.emit(Event(type="tool_use", data={"id": "tool-1", "name": "search"}))
    await observer.emit(
        Event(
            type="tool_result",
            data={
                "id": "tool-1",
                "content": "<persisted-output>placeholder</persisted-output>",
                "_citation_content": json.dumps(evidence),
            },
        )
    )
    await observer.emit(
        Event(
            type="assistant_message",
            data={
                "text": "Cloud revenue increased by 20% [1](evidence://ev_large_persisted_2025)."
            },
        )
    )
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))

    tool_result = next(event for event in store.appended if event.type == "tool_result")
    assert "_citation_content" not in tool_result.data
    assert "padding" not in str(tool_result.data)
    assert "padding" not in str(next(e for e in live.events if e.type == "tool_result").data)
    assistant = next(event for event in store.appended if event.type == "assistant_message")
    assert len(assistant.data["citation_bundle"]["citations"]) == 1
    assert assistant.data["citation_bundle"]["integrity"]["evidenceRejectedCount"] == 0


async def test_source_tool_result_persists_compact_evidence_but_seals_full_snapshot() -> None:
    store, live, observer = _observer_with_citations()
    payload = {
        "_valuz_evidence": {
            "evidenceHandle": "ev_compact_revenue_2025",
            "source": {
                "sourceId": "financials:issuer",
                "providerId": "valuz-stock",
                "sourceType": "dataset",
                "title": "Income statement",
                "retrievedAt": "2026-08-01T08:00:00Z",
            },
            "evidence": {
                "kind": "structured-data",
                "datasetId": "financials",
                "toolName": "income_statement",
                "recordKey": "issuer|FY2025",
                "field": "revenue",
                "metric": "revenue",
                "value": 120,
                "unit": "USDm",
                "period": "FY2025",
                "capturedAt": "2026-08-01T08:00:00Z",
            },
        },
        "data": [{"revenue": 120}],
    }
    await observer.emit(Event(type="tool_use", data={"id": "tool-1", "name": "income"}))
    await observer.emit(
        Event(
            type="tool_result",
            data={"id": "tool-1", "content": json.dumps(payload)},
        )
    )
    persisted = next(event for event in store.appended if event.type == "tool_result")
    visible = json.loads(persisted.data["content"])
    hint = visible["_valuz_evidence_hint"]
    await observer.emit(
        Event(
            type="assistant_message",
            data={
                "text": (
                    "FY2025 revenue was 120 USDm "
                    f"[1](evidence://{hint['collectionHandle']}#/data/0/revenue)."
                )
            },
        )
    )
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))

    assert "_valuz_evidence" not in visible
    assert visible["data"] == payload["data"]
    assert hint["collectionHandle"].startswith("evc_legacy_")
    assert hint["citationTemplate"].endswith("#{json-pointer}")
    assert visible["data"] == [{"revenue": 120}]
    assert "providerId" not in persisted.data["content"]
    assert (
        "providerId"
        not in next(event for event in live.events if event.type == "tool_result").data["content"]
    )
    assistant = next(event for event in store.appended if event.type == "assistant_message")
    citation = assistant.data["citation_bundle"]["citations"][0]
    assert citation["source"]["providerId"] == "valuz-stock"
    assert citation["evidence"]["capturedAt"] == "2026-08-01T08:00:00Z"


async def test_unresolved_claim_is_published_without_hidden_repair() -> None:
    store, live, observer = _observer_with_citations()
    evidence = {
        "_valuz_evidence": {
            "evidenceHandle": "ev_unrelated_2025",
            "source": {
                "sourceId": "doc-1",
                "providerId": "docs",
                "documentId": "doc-1",
                "sourceType": "document",
                "title": "Report",
                "retrievedAt": "2026-08-02T10:00:00Z",
            },
            "evidence": {
                "kind": "text",
                "quote": "Capital expenditure remained within budget.",
                "capturedAt": "2026-08-02T10:00:00Z",
            },
            "locator": {"kind": "pdf", "page": 1},
        }
    }
    await observer.emit(Event(type="tool_use", data={"id": "tool-1", "name": "doc_search"}))
    await observer.emit(
        Event(type="tool_result", data={"id": "tool-1", "content": json.dumps(evidence)})
    )
    await observer.emit(
        Event(type="assistant_message", data={"text": "Revenue was 120 USD in 2025."})
    )
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))

    assert observer.citation_repair_requested is False
    assistant = next(event for event in store.appended if event.type == "assistant_message")
    assert assistant.data["text"] == "Revenue was 120 USD in 2025."
    assert any(event.type == "assistant_message" for event in live.events)
    claims = assistant.data["citation_bundle"]["quality"]["claims"]
    unresolved = next(claim for claim in claims if claim["citationRequired"])
    assert unresolved["status"] in {"unsupported", "unverified"}


async def test_citation_only_mode_discards_unknown_marker_without_hidden_repair() -> None:
    store, _live, observer = _observer_with_citations(verification_enabled=False)

    await observer.emit(
        Event(
            type="assistant_message",
            data={"text": "Revenue was 120 USD [source](evidence://W123456789)."},
        )
    )
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))

    assert observer.citation_repair_requested is False
    assistant = next(event for event in store.appended if event.type == "assistant_message")
    assert assistant.data["text"] == "Revenue was 120 USD."


async def test_confirmed_entity_conflict_requests_bounded_local_repair() -> None:
    _store, _live, observer = _observer_with_citations()
    evidence = {
        "_valuz_evidence": {
            "evidenceHandle": "ev_wrong_entity_12345678",
            "source": {
                "sourceId": "income-000858",
                "providerId": "data",
                "sourceType": "dataset",
                "title": "五粮液（000858）收入",
                "retrievedAt": "2026-08-02T10:00:00Z",
            },
            "evidence": {
                "kind": "structured-data",
                "datasetId": "financials",
                "toolName": "income_statement",
                "recordKey": "000858|2025 FY",
                "entityId": "000858",
                "field": "revenue",
                "metric": "revenue",
                "value": 120,
                "unit": "USD",
                "period": "2025 FY",
                "capturedAt": "2026-08-02T10:00:00Z",
            },
        }
    }
    observer._evidence_registry.register_tool_result(
        json.dumps(evidence, ensure_ascii=False),
        tool_name="stock",
    )
    await observer.emit(
        Event(
            type="assistant_message",
            data={
                "text": (
                    "贵州茅台（600519）2025 年 revenue 为 "
                    "120 USD [source](evidence://ev_wrong_entity_12345678)。"
                )
            },
        )
    )
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))

    assert observer.citation_repair_requested is True
    context = json.loads(
        observer.citation_repair_prompt.split("Restricted repair context (JSON):\n", 1)[1]
    )
    assert len(context["claimIssues"]) == 1
    assert context["claimIssues"][0]["issueCodes"] == ["claim_source_entity_conflict"]
    assert context["claimIssues"][0]["candidateHandles"] == ["ev_wrong_entity_12345678"]
    assert [row["evidenceHandle"] for row in context["candidateEvidence"]] == [
        "ev_wrong_entity_12345678"
    ]


async def test_source_free_general_knowledge_does_not_trigger_repair() -> None:
    store, _live, observer = _observer_with_strict_policy()

    await observer.emit(
        Event(
            type="assistant_message",
            data={"text": "ROE 是衡量股东资本回报效率的常用指标。"},
        )
    )
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))

    assert observer.citation_repair_requested is False
    assistant = next(event for event in store.appended if event.type == "assistant_message")
    assert "ROE" in assistant.data["text"]


async def test_unresolved_delta_draft_remains_visible_without_second_model_pass() -> None:
    store, live, observer = _observer_with_citations()

    await observer.emit(Event(type="text_delta", data={"text": "Revenue was 120 USD in 2025."}))
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))

    assert observer.citation_repair_requested is False
    assistant = next(event for event in store.appended if event.type == "assistant_message")
    assert assistant.data["text"] == "Revenue was 120 USD in 2025."
    assert any(event.type == "assistant_message" for event in live.events)


def test_verbose_repair_may_remove_only_unsupported_scope_expansion() -> None:
    supported_claims = [
        {
            "citationRequired": True,
            "status": "passed",
            "issueCodes": [],
        }
        for _ in range(20)
    ]
    unsupported_claims = [
        {
            "citationRequired": True,
            "status": "degraded",
            "issueCodes": ["claim_without_citation"],
        }
        for _ in range(10)
    ]
    baseline = Event(
        type="assistant_message",
        data={
            "citation_bundle": {
                "integrity": {},
                "quality": {
                    "metrics": {"unsourcedClaimCount": 10},
                    "claims": supported_claims + unsupported_claims,
                },
            }
        },
    )
    candidate = Event(
        type="assistant_message",
        data={
            "citation_bundle": {
                "integrity": {},
                "quality": {
                    "metrics": {"unsourcedClaimCount": 0},
                    "claims": supported_claims,
                },
            }
        },
    )

    assert _MessageObserverSink._repair_improves(baseline, candidate) is True


def test_repair_can_replace_unknown_markers_with_fewer_explicit_mismatches() -> None:
    baseline = Event(
        type="assistant_message",
        data={
            "citation_bundle": {
                "integrity": {"unknownCitationIds": [f"doc-{i}" for i in range(10)]},
                "quality": {
                    "metrics": {
                        "unsourcedClaimCount": 28,
                        "unverifiedClaimCount": 0,
                        "claimSemanticMismatchCount": 0,
                    },
                    "claims": [
                        {
                            "citationRequired": True,
                            "status": "unsupported",
                            "issueCodes": ["claim_without_citation"],
                        }
                        for _ in range(28)
                    ],
                },
            }
        },
    )
    candidate_claims = (
        [{"citationRequired": True, "status": "passed", "issueCodes": []} for _ in range(7)]
        + [
            {
                "citationRequired": True,
                "status": "unverified",
                "issueCodes": ["claim_evidence_mismatch"],
            }
            for _ in range(14)
        ]
        + [
            {
                "citationRequired": True,
                "status": "unsupported",
                "issueCodes": ["claim_without_citation"],
            }
        ]
    )
    candidate = Event(
        type="assistant_message",
        data={
            "citation_bundle": {
                "integrity": {"unknownCitationIds": []},
                "quality": {
                    "metrics": {
                        "unsourcedClaimCount": 1,
                        "unverifiedClaimCount": 14,
                        "claimSemanticMismatchCount": 14,
                    },
                    "claims": candidate_claims,
                },
            }
        },
    )

    assert _MessageObserverSink._repair_improves(baseline, candidate) is True


def test_small_repair_cannot_drop_one_unsupported_requested_claim() -> None:
    passed = {"citationRequired": True, "status": "passed", "issueCodes": []}
    failed = {
        "citationRequired": True,
        "status": "degraded",
        "issueCodes": ["claim_without_citation"],
    }
    baseline = Event(
        type="assistant_message",
        data={
            "citation_bundle": {
                "integrity": {},
                "quality": {
                    "metrics": {"unsourcedClaimCount": 1},
                    "claims": [passed, failed],
                },
            }
        },
    )
    candidate = Event(
        type="assistant_message",
        data={
            "citation_bundle": {
                "integrity": {},
                "quality": {
                    "metrics": {"unsourcedClaimCount": 0},
                    "claims": [passed],
                },
            }
        },
    )

    assert _MessageObserverSink._repair_improves(baseline, candidate) is False


def test_small_repair_cannot_replace_a_requested_value_with_a_limitation() -> None:
    failed = {
        "citationRequired": True,
        "status": "unverified",
        "issueCodes": ["claim_evidence_mismatch"],
    }
    passed = {"citationRequired": True, "status": "passed", "issueCodes": []}
    baseline = Event(
        type="assistant_message",
        data={
            "text": (
                "| 指标 | 金额 |\n|---|---|\n"
                "| 营业收入 | 1,708.99 亿元 |\n"
                "| 归母净利润 | 862.28 亿元 |\n"
                "| 归母净利率 | 50.46% |"
            ),
            "citation_bundle": {
                "integrity": {},
                "quality": {
                    "metrics": {"unverifiedClaimCount": 2},
                    "claims": [passed, failed, failed],
                },
            },
        },
    )
    candidate = Event(
        type="assistant_message",
        data={
            "text": (
                "| 指标 | 金额 |\n|---|---|\n"
                "| 营业收入 | 1,708.99 亿元 |\n"
                "| 归母净利润 | 暂无法引用具体数值 |\n"
                "| 归母净利率 | 50.46% |"
            ),
            "citation_bundle": {
                "integrity": {},
                "quality": {
                    "metrics": {"unverifiedClaimCount": 1},
                    "claims": [passed, passed, failed],
                },
            },
        },
    )

    assert _MessageObserverSink._repair_improves(baseline, candidate) is False


def test_medium_repair_may_remove_redundant_failed_claims() -> None:
    baseline_claims = [
        {"citationRequired": True, "status": "passed", "issueCodes": []} for _ in range(4)
    ] + [
        {
            "citationRequired": True,
            "status": "unverified",
            "issueCodes": ["claim_evidence_mismatch"],
        }
        for _ in range(6)
    ]
    candidate_claims = [
        {"citationRequired": True, "status": "passed", "issueCodes": []} for _ in range(4)
    ] + [
        {
            "citationRequired": True,
            "status": "unverified",
            "issueCodes": ["claim_evidence_mismatch"],
        }
        for _ in range(2)
    ]
    baseline = Event(
        type="assistant_message",
        data={
            "citation_bundle": {
                "integrity": {},
                "quality": {
                    "metrics": {"unverifiedClaimCount": 6},
                    "claims": baseline_claims,
                },
            }
        },
    )
    candidate = Event(
        type="assistant_message",
        data={
            "citation_bundle": {
                "integrity": {},
                "quality": {
                    "metrics": {"unverifiedClaimCount": 2},
                    "claims": candidate_claims,
                },
            }
        },
    )

    assert _MessageObserverSink._repair_improves(baseline, candidate) is True


def test_strict_scope_repair_may_remove_bloated_unsupported_expansion() -> None:
    passed = {"citationRequired": True, "status": "passed", "issueCodes": []}
    failed = {
        "citationRequired": True,
        "status": "unverified",
        "issueCodes": ["claim_evidence_mismatch"],
    }
    baseline = Event(
        type="assistant_message",
        data={
            "text": "4.55亿元、36.51亿元，并附十项未请求解释。",
            "citation_bundle": {
                "integrity": {},
                "quality": {
                    "metrics": {"unsourcedClaimCount": 5, "unverifiedClaimCount": 3},
                    "claims": [passed, passed, *[failed for _ in range(8)]],
                },
            },
        },
    )
    candidate = Event(
        type="assistant_message",
        data={
            "text": "扣非净利润：4.55亿元。商誉：原文未能确认具体余额。",
            "citation_bundle": {
                "integrity": {},
                "quality": {
                    "metrics": {"unsourcedClaimCount": 0, "unverifiedClaimCount": 1},
                    "claims": [passed, passed, passed, failed],
                },
            },
        },
    )

    assert (
        _MessageObserverSink._repair_improves(
            baseline,
            candidate,
            strict_output_scope=True,
        )
        is True
    )
    assert _MessageObserverSink._repair_improves(baseline, candidate) is False


def test_large_repair_cannot_replace_all_business_values_with_a_limitation() -> None:
    failed = {
        "citationRequired": True,
        "status": "unverified",
        "issueCodes": ["claim_evidence_mismatch"],
    }
    baseline = Event(
        type="assistant_message",
        data={
            "text": (
                "茅台酒 1,459.28 亿元、+15.28%；系列酒 246.84 亿元、+19.65%；"
                "直销 748.43 亿元、+11.32%；批发代理 957.69 亿元、+19.73%。"
            ),
            "citation_bundle": {
                "integrity": {},
                "quality": {
                    "metrics": {"unverifiedClaimCount": 8},
                    "claims": [failed for _ in range(8)],
                },
            },
        },
    )
    candidate = Event(
        type="assistant_message",
        data={
            "text": "当前来源覆盖不足，暂时无法列示具体数字。",
            "citation_bundle": {
                "integrity": {},
                "quality": {
                    "metrics": {"unverifiedClaimCount": 1},
                    "claims": [failed for _ in range(4)],
                },
            },
        },
    )

    assert _MessageObserverSink._repair_improves(baseline, candidate) is False


async def test_large_research_history_publishes_unresolved_claim_without_repair() -> None:
    store, _live, observer = _observer_with_citations()
    evidence = {
        "_valuz_evidence": {
            "evidenceHandle": "ev_revenue_2025",
            "source": {
                "sourceId": "doc-1",
                "providerId": "docs",
                "documentId": "doc-1",
                "sourceType": "document",
                "title": "Report",
                "retrievedAt": "2026-07-30T10:00:00Z",
            },
            "evidence": {
                "kind": "text",
                "quote": "Revenue increased by 20%.",
                "snippet": "Revenue increased by 20%.",
                "capturedAt": "2026-07-30T10:00:00Z",
            },
            "locator": {"kind": "chunk", "chunkId": "chunk-1"},
        }
    }
    await observer.emit(Event(type="tool_use", data={"id": "tool-1", "name": "docs"}))
    await observer.emit(
        Event(type="tool_result", data={"id": "tool-1", "content": json.dumps(evidence)})
    )
    await observer.emit(
        Event(
            type="usage_update",
            data={"input_tokens": 250_001, "output_tokens": 100},
        )
    )
    await observer.emit(Event(type="assistant_message", data={"text": "CEO is Alice."}))
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))

    assert observer.citation_repair_requested is False
    assistant = next(event for event in store.appended if event.type == "assistant_message")
    assert assistant.data["text"] == "CEO is Alice."


async def test_cached_research_input_does_not_skip_a_bounded_repair() -> None:
    _store, _live, observer = _observer_with_citations()
    observer.usage = {
        "input_tokens": 279_434,
        "output_tokens": 3_090,
        "cache_read_tokens": 91_846,
        "cache_write_tokens": 0,
    }
    bundle = {
        "quality": {
            "claims": [
                {
                    "citationRequired": True,
                    "issueCodes": ["numeric_claim_without_citation"],
                }
                for _ in range(12)
            ]
        }
    }

    assert observer._citation_repair_skip_reason(bundle, "draft") is None


async def test_repair_claim_budget_skips_more_claims_than_one_patch_can_fix() -> None:
    _store, _live, observer = _observer_with_citations()
    bundle = {
        "quality": {
            "claims": [
                {
                    "citationRequired": True,
                    "issueCodes": ["claim_without_citation"],
                }
                for _ in range(15)
            ]
        }
    }

    assert observer._citation_repair_skip_reason(bundle, "draft") == "claim-count-budget"


async def test_advisory_and_missing_issues_do_not_create_repair_candidates() -> None:
    _store, _live, observer = _observer_with_citations()
    advisory_claims = [
        {
            "claimId": f"claim_advisory_{index}",
            "exact": f"公开二手资料表述 {index}",
            "citationRequired": True,
            "issueCodes": ["low_tier_without_cross_check"],
        }
        for index in range(20)
    ]
    missing_claims = [
        {
            "claimId": f"claim_missing_{index}",
            "exact": f"缺少引用的事实 {index}",
            "citationRequired": True,
            "issueCodes": ["claim_without_citation"],
        }
        for index in range(2)
    ]
    bundle = {
        "quality": {
            "claims": [*advisory_claims, *missing_claims],
            "issues": [
                {"code": "low_tier_without_cross_check"},
                {"code": "claim_without_citation"},
            ],
            "metrics": {"unsourcedClaimCount": 2, "unverifiedClaimCount": 0},
        }
    }
    draft = "公开二手资料表述。缺少引用的事实 0。缺少引用的事实 1。"

    prompt = observer._build_citation_repair_prompt(bundle, draft)
    context = json.loads(prompt.split("Restricted repair context (JSON):\n", 1)[1])

    assert (
        observer._citation_repair_skip_reason(
            bundle,
            draft,
            repair_prompt=prompt,
        )
        == "no-actionable-resolution"
    )
    assert context["claimIssues"] == []
    assert context["candidateEvidence"] == []
    assert context["generalIssues"] == []


async def test_advisory_only_quality_does_not_request_hidden_repair() -> None:
    _store, _live, observer = _observer_with_citations()
    advisory_bundle = {
        "citations": [{"citationId": "cit_news"}],
        "integrity": {},
        "quality": {
            "claims": [
                {
                    "citationRequired": True,
                    "issueCodes": ["low_tier_without_cross_check"],
                }
            ],
            "issues": [{"code": "low_tier_without_cross_check"}],
            "metrics": {"unsourcedClaimCount": 0, "unverifiedClaimCount": 0},
        },
    }
    missing_bundle = {
        **advisory_bundle,
        "quality": {
            **advisory_bundle["quality"],
            "claims": [
                {
                    "citationRequired": True,
                    "issueCodes": ["claim_without_citation"],
                }
            ],
            "metrics": {"unsourcedClaimCount": 1, "unverifiedClaimCount": 0},
        },
    }

    assert observer._citation_publication_needs_repair(advisory_bundle) is False
    assert observer._citation_publication_needs_repair(missing_bundle) is False


async def test_unresolved_claim_never_catalogues_the_entire_evidence_pool() -> None:
    _store, _live, observer = _observer_with_citations()
    evidence = {
        "_valuz_evidence": {
            "evidenceHandle": "ev_shared_revenue_2025",
            "source": {
                "sourceId": "doc-1",
                "providerId": "docs",
                "documentId": "doc-1",
                "sourceType": "document",
                "title": "Report",
                "retrievedAt": "2026-07-30T10:00:00Z",
            },
            "evidence": {
                "kind": "text",
                "quote": "Revenue was 120 USD and profit was 30 USD in 2025.",
                "snippet": "Revenue was 120 USD and profit was 30 USD in 2025.",
                "capturedAt": "2026-07-30T10:00:00Z",
            },
            "locator": {"kind": "chunk", "chunkId": "chunk-1"},
        }
    }
    await observer.emit(Event(type="tool_use", data={"id": "tool-1", "name": "docs"}))
    await observer.emit(
        Event(type="tool_result", data={"id": "tool-1", "content": json.dumps(evidence)})
    )
    duplicate = json.loads(json.dumps(evidence))
    duplicate["_valuz_evidence"]["evidenceHandle"] = "ev_shared_revenue_duplicate"
    duplicate["_valuz_evidence"]["source"]["sourceId"] = "doc-2"
    duplicate["_valuz_evidence"]["source"]["documentId"] = "doc-2"
    await observer.emit(Event(type="tool_use", data={"id": "tool-2", "name": "docs"}))
    await observer.emit(
        Event(type="tool_result", data={"id": "tool-2", "content": json.dumps(duplicate)})
    )
    await observer.emit(
        Event(
            type="assistant_message",
            data={
                "text": (
                    "Management reported revenue was 120 USD. "
                    "Management reported profit was 30 USD."
                )
            },
        )
    )
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))

    assert observer.citation_repair_requested is False


async def test_non_actionable_repair_context_excludes_registry_records() -> None:
    _store, _live, observer = _observer_with_citations()
    observer._evidence_registry.register_tool_result(
        json.dumps(
            {
                "_valuz_evidence": {
                    "evidenceHandle": "ev_unrelated_12345678",
                    "source": {
                        "sourceId": "doc-1",
                        "providerId": "docs",
                        "sourceType": "document",
                        "title": "Unrelated report",
                        "retrievedAt": "2026-08-02T10:00:00Z",
                    },
                    "evidence": {
                        "kind": "text",
                        "quote": "An unrelated fact.",
                        "snippet": "An unrelated fact.",
                        "capturedAt": "2026-08-02T10:00:00Z",
                    },
                }
            }
        ),
        tool_name="document_fetch",
    )
    bundle = {
        "quality": {
            "claims": [
                {
                    "claimId": "claim-unresolved",
                    "exact": "Revenue was 120 USD.",
                    "citationRequired": True,
                    "issueCodes": ["claim_evidence_mismatch"],
                    "citationIds": [],
                }
            ],
            "issues": [{"code": "claim_evidence_mismatch"}],
        }
    }

    prompt = observer._build_citation_repair_prompt(bundle, "Revenue was 120 USD.")
    context = json.loads(prompt.split("Restricted repair context (JSON):\n", 1)[1])

    assert context["claimIssues"] == []
    assert context["candidateEvidence"] == []
    assert (
        observer._citation_repair_skip_reason(
            bundle,
            "Revenue was 120 USD.",
            repair_prompt=prompt,
        )
        == "no-actionable-resolution"
    )

async def test_repair_claim_budget_still_blocks_pathological_drafts() -> None:
    _store, _live, observer = _observer_with_citations()
    bundle = {
        "quality": {
            "claims": [
                {
                    "citationRequired": True,
                    "issueCodes": ["claim_without_citation"],
                }
                for _ in range(65)
            ]
        }
    }

    assert observer._citation_repair_skip_reason(bundle, "draft") == "claim-count-budget"


async def test_repair_claim_budget_matches_the_claim_patch_capacity() -> None:
    _store, _live, observer = _observer_with_citations()

    def bundle_with_failures(count: int) -> dict:
        return {
            "quality": {
                "claims": [
                    {
                        "citationRequired": True,
                        "issueCodes": ["claim_without_citation"],
                    }
                    for _ in range(count)
                ]
            }
        }

    assert observer._citation_repair_skip_reason(bundle_with_failures(12), "draft") is None
    assert (
        observer._citation_repair_skip_reason(bundle_with_failures(13), "draft")
        == "claim-count-budget"
    )


async def test_repair_claim_budget_uses_quality_metrics_when_claim_list_is_partial() -> None:
    _store, _live, observer = _observer_with_citations()
    bundle = {
        "quality": {
            "claims": [
                {"citationRequired": True, "issueCodes": ["claim_without_citation"]}
                for _ in range(8)
            ],
            "metrics": {
                "unsourcedClaimCount": 29,
                "unverifiedClaimCount": 0,
            },
        }
    }

    assert observer._citation_repair_skip_reason(bundle, "compact draft") == "claim-count-budget"


async def test_advisory_translation_rows_do_not_consume_repair_claim_budget() -> None:
    _store, _live, observer = _observer_with_citations()
    repairable = [
        {"citationRequired": True, "issueCodes": ["claim_without_citation"]}
        for _ in range(3)
    ]
    advisory = [
        {
            "citationRequired": True,
            "issueCodes": ["claim_translation_not_verified"],
        }
        for _ in range(20)
    ]
    claims = repairable + advisory
    bundle = {
        "quality": {
            "claims": claims,
            "metrics": {
                "claimDetectedCount": len(claims),
                "claimAuditTruncated": False,
                "unsourcedClaimCount": 3,
                "unverifiedClaimCount": 20,
            },
        }
    }

    assert observer._citation_repair_skip_reason(bundle, "compact draft") is None


async def test_discovery_only_draft_still_respects_claim_budget() -> None:
    _store, _live, observer = _observer_with_citations()
    bundle = {
        "integrity": {
            "unknownCitationIds": ["W123456789"],
            "evidenceRegisteredCount": 0,
        },
        "quality": {
            "claims": [
                {
                    "citationRequired": True,
                    "issueCodes": ["claim_without_citation"],
                }
                for _ in range(31)
            ]
        },
    }

    assert observer._citation_repair_skip_reason(bundle, "compact draft") == "claim-count-budget"


async def test_zero_evidence_answer_is_published_without_hidden_repair() -> None:
    store, _live, observer = _observer_with_strict_policy()
    await observer.emit(
        Event(
            type="assistant_message",
            data={
                "text": "产品价格上涨 300% [1](evidence://W123456789)。",
            },
        )
    )
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))
    assert observer.citation_repair_requested is False
    assistant = next(event for event in store.appended if event.type == "assistant_message")
    assert "300%" in assistant.data["text"]
    assert "citation_bundle" in assistant.data
    assert "W123456789" not in assistant.data["text"]


async def test_uncited_strict_answer_is_published_without_second_model_pass() -> None:
    store, live, observer = _observer_with_strict_policy()
    await observer.emit(
        Event(
            type="assistant_message",
            data={"text": "营业收入为 120 亿元。"},
        )
    )
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))
    assert observer.citation_repair_requested is False
    assistant = next(event for event in store.appended if event.type == "assistant_message")
    assert assistant.data["text"] == "营业收入为 120 亿元。"
    assert [event.type for event in live.events].count("assistant_message") == 1


async def test_wrong_calendar_binding_is_rebound_to_unique_transcript_without_repair() -> None:
    store, _live, observer = _observer_with_strict_policy()
    evidences = [
        {
            "evidenceHandle": "ev_calendar_2025",
            "source": {
                "sourceId": "calendar-msft",
                "providerId": "stock",
                "sourceType": "dataset",
                "title": "Stock earnings calendar · MSFT",
                "retrievedAt": "2026-08-01T08:00:00Z",
            },
            "evidence": {
                "kind": "structured-data",
                "datasetId": "calendar",
                "toolName": "earnings_calendar",
                "recordKey": "MSFT|2025 FY",
                "field": "filing_date",
                "value": "2025-07-30",
                "period": "2025 FY",
                "capturedAt": "2026-08-01T08:00:00Z",
            },
        },
        {
            "evidenceHandle": "ev_transcript_cloud_2025",
            "source": {
                "sourceId": "transcript-msft-q4",
                "providerId": "search",
                "documentId": "transcript-msft-q4",
                "sourceType": "document",
                "title": "Microsoft Q4 earnings call transcript",
                "retrievedAt": "2026-08-01T08:00:00Z",
            },
            "evidence": {
                "kind": "text",
                "quote": "Microsoft cloud revenue increased by 20% in 2025 Q4.",
                "snippet": "Microsoft cloud revenue increased by 20% in 2025 Q4.",
                "capturedAt": "2026-08-01T08:00:00Z",
            },
            "locator": {"kind": "chunk", "chunkId": "q4-cloud"},
        },
    ]
    await observer.emit(Event(type="tool_use", data={"id": "tool-1", "name": "search"}))
    await observer.emit(
        Event(
            type="tool_result",
            data={"id": "tool-1", "content": json.dumps({"_valuz_evidence": evidences})},
        )
    )
    await observer.emit(
        Event(
            type="assistant_message",
            data={
                "text": (
                    "Microsoft cloud revenue increased by 20% in 2025 Q4 "
                    "[1](evidence://ev_calendar_2025)."
                )
            },
        )
    )
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))

    assert observer.citation_repair_requested is False
    assistant = next(event for event in store.appended if event.type == "assistant_message")
    citations = assistant.data["citation_bundle"]["citations"]
    assert [citation["source"]["sourceId"] for citation in citations] == ["transcript-msft-q4"]
    assert citations[0]["annotations"]["binding"]["autoReboundClaimIds"]


async def test_partial_after_a_canonical_block_is_persisted_separately() -> None:
    store, live, observer = _observer()

    await observer.emit(Event(type="text_delta", data={"text": "draft one"}))
    await observer.emit(Event(type="assistant_message", data={"text": "final one"}))
    await observer.emit(Event(type="text_delta", data={"text": "partial two"}))
    await observer.emit(
        Event(
            type="session_idle",
            data={
                "stop_reason": {"type": "error", "category": "user_interrupt"},
                "num_turns": 1,
            },
        )
    )

    assert [event.type for event in store.appended] == [
        "assistant_message",
        "assistant_message",
        "session_idle",
    ]
    assert store.appended[0].data == {"text": "final one"}
    assert store.appended[1].data == {"text": "partial two"}
    assert observer.assistant_text == "final one\npartial two"
    assert [event.type for event in live.events] == [
        "text_delta",
        "assistant_message",
        "text_delta",
        "assistant_message",
        "session_idle",
    ]
