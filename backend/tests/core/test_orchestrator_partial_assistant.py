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
    _sanitize_citation_repair_prose,
    _strip_empty_markdown_labels,
    _strip_empty_markdown_tables,
    _strip_unrequested_derived_restatement,
    _strip_leading_assistant_progress,
    _strip_strict_scope_leadin,
    _strip_unrequested_source_excerpt,
    _strip_unrequested_retrieval_internals,
)


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


def _observer_with_citations() -> tuple[_FakeStore, _RecordingSink, _MessageObserverSink]:
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
    await observer.emit(
        Event(
            type="assistant_message",
            data={"text": "FY2025 revenue was 120 USDm [1](evidence://ev_compact_revenue_2025)."},
        )
    )
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))

    persisted = next(event for event in store.appended if event.type == "tool_result")
    visible = json.loads(persisted.data["content"])
    assert visible["_valuz_evidence"] == [
        {
            "evidenceHandle": "ev_compact_revenue_2025",
            "kind": "structured-data",
            "field": "revenue",
            "metric": "revenue",
            "value": 120,
            "unit": "USDm",
            "period": "FY2025",
            "recordKey": "issuer|FY2025",
            "sourceTitle": "Income statement",
            "citationLink": "[source](evidence://ev_compact_revenue_2025)",
        }
    ]
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


async def test_uncited_evidence_answer_is_withheld_then_repaired_once() -> None:
    store, live, observer = _observer_with_citations()
    evidence = {
        "_valuz_evidence": {
            "evidenceHandle": "ev_retry_2025",
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
            data={"text": "Revenue was 120 USD in 2025."},
        )
    )
    await observer.emit(
        Event(
            type="session_idle",
            data={"stop_reason": {"type": "end_turn"}, "num_turns": 1},
        )
    )

    assert observer.citation_repair_requested is True
    assert "Do not rewrite the answer" in observer.citation_repair_prompt
    assert "Return JSON only" in observer.citation_repair_prompt
    assert "unknown handle is rejected" in observer.citation_repair_prompt
    assert not any(event.type == "assistant_message" for event in store.appended)
    assert not any(event.type == "session_idle" for event in store.appended)
    observer.begin_citation_repair()

    await observer.emit(
        Event(
            type="assistant_message",
            data={
                "text": _claim_patch_json(
                    observer,
                    replacement_text="Revenue increased.",
                    evidence_handles=["ev_retry_2025"],
                )
            },
        )
    )
    await observer.emit(
        Event(
            type="session_idle",
            data={"stop_reason": {"type": "end_turn"}, "num_turns": 1},
        )
    )

    assistant = next(event for event in store.appended if event.type == "assistant_message")
    assert assistant.data["text"].startswith("Revenue increased. [1](citation://cit_")
    assert assistant.data["citation_bundle"]["integrity"]["status"] == "repaired"
    assert assistant.data["citation_bundle"]["integrity"]["repairAttempts"] == 1
    assert [event.type for event in store.appended].count("session_idle") == 1
    assert [event.type for event in live.events].count("session_idle") == 1


async def test_citation_only_mode_discards_unknown_marker_without_hidden_repair() -> None:
    store = _FakeStore()
    live = _RecordingSink()
    db = DatabaseEventSink(store, "owner-1", "sess-1", "msg-1")
    observer = _MessageObserverSink(
        DeltaCoalescingSink(PersistThenBroadcastSink(db, live)),
        message_id="msg-1",
        user_prompt="根据文档回答并引用",
        citation_policy_available=True,
        citation_verification_enabled=False,
    )
    evidence = {
        "_valuz_evidence": {
            "evidenceHandle": "ev_known_2025",
            "source": {
                "sourceId": "doc-1",
                "providerId": "docs",
                "documentId": "doc-1",
                "sourceType": "document",
                "title": "Report",
                "retrievedAt": "2026-08-01T08:00:00Z",
            },
            "evidence": {
                "kind": "text",
                "quote": "Revenue increased 20%.",
                "snippet": "Revenue increased 20%.",
                "capturedAt": "2026-08-01T08:00:00Z",
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
            data={
                "text": (
                    "Revenue increased 20% [1](evidence://ev_known_2025). "
                    "Untrusted marker [2](evidence://ev_unknown_2025)."
                )
            },
        )
    )
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))

    assert observer.citation_repair_requested is False
    assistant = next(event for event in store.appended if event.type == "assistant_message")
    assert "citation://cit_" in assistant.data["text"]
    assert "evidence://" not in assistant.data["text"]
    assert assistant.data["citation_bundle"]["integrity"]["unknownCitationIds"] == [
        "ev_unknown_2025"
    ]
    assert [event.type for event in store.appended].count("session_idle") == 1


async def test_interrupted_repair_discards_partial_and_publishes_sealed_baseline() -> None:
    store, live, observer = _observer_with_citations()
    evidence = {
        "_valuz_evidence": {
            "evidenceHandle": "ev_abort_2025",
            "source": {
                "sourceId": "doc-abort",
                "providerId": "docs",
                "documentId": "doc-abort",
                "sourceType": "document",
                "title": "Report",
                "retrievedAt": "2026-08-01T08:00:00Z",
            },
            "evidence": {
                "kind": "text",
                "quote": "Revenue increased.",
                "snippet": "Revenue increased.",
                "capturedAt": "2026-08-01T08:00:00Z",
            },
            "locator": {"kind": "pdf", "page": 1},
        }
    }
    await observer.emit(Event(type="tool_use", data={"id": "tool-1", "name": "doc_search"}))
    await observer.emit(
        Event(type="tool_result", data={"id": "tool-1", "content": json.dumps(evidence)})
    )
    await observer.emit(Event(type="assistant_message", data={"text": "Uncited draft."}))
    await observer.emit(
        Event(
            type="session_idle",
            data={"stop_reason": {"type": "end_turn"}, "num_turns": 1},
        )
    )
    assert observer.citation_repair_requested is True
    observer.begin_citation_repair()

    await observer.emit(
        Event(
            type="assistant_message",
            data={
                "text": (
                    "Repair failed with evidenceHandle ev_abort_2025 and 401 Invalid credentials."
                )
            },
        )
    )
    await observer.emit(
        Event(
            type="session_idle",
            data={
                "stop_reason": {"type": "error", "category": "user_interrupt"},
                "num_turns": 1,
            },
        )
    )

    assistants = [event for event in store.appended if event.type == "assistant_message"]
    assert len(assistants) == 1
    assert assistants[0].data["text"] == "Uncited draft."
    assert "evidenceHandle" not in assistants[0].data["text"]
    assert "Invalid credentials" not in assistants[0].data["text"]
    integrity = assistants[0].data["citation_bundle"]["integrity"]
    assert integrity["repairOutcome"] == "aborted"
    assert integrity["repairAbortReason"] == "error"
    assert integrity["repairAttempts"] == 1
    assert [event.type for event in live.events].count("assistant_message") == 1
    assert [event.type for event in live.events].count("session_idle") == 1


async def test_one_valid_citation_does_not_hide_another_uncited_claim() -> None:
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
    await observer.emit(Event(type="tool_use", data={"id": "tool-1", "name": "doc_search"}))
    await observer.emit(
        Event(type="tool_result", data={"id": "tool-1", "content": json.dumps(evidence)})
    )
    await observer.emit(
        Event(
            type="assistant_message",
            data={
                "text": (
                    "Revenue increased [report](evidence://ev_revenue_2025). Alice is the CEO."
                )
            },
        )
    )
    await observer.emit(
        Event(
            type="session_idle",
            data={"stop_reason": {"type": "end_turn"}, "num_turns": 1},
        )
    )

    assert observer.citation_repair_requested is True
    assert not any(event.type == "assistant_message" for event in store.appended)


async def test_strict_policy_repairs_claim_local_quality_issue_even_with_valid_citation() -> None:
    store, _live, observer = _observer_with_strict_policy()
    evidence = {
        "_valuz_evidence": {
            "evidenceHandle": "ev_strict_revenue_2025",
            "source": {
                "sourceId": "financials",
                "providerId": "data",
                "sourceType": "dataset",
                "title": "Income statement",
                "retrievedAt": "2026-08-01T08:00:00Z",
            },
            "evidence": {
                "kind": "structured-data",
                "datasetId": "financials",
                "toolName": "income_statement",
                "recordKey": "issuer|2025 FY",
                "field": "revenue",
                "value": 120,
                "period": "2025 FY",
                "capturedAt": "2026-08-01T08:00:00Z",
            },
        }
    }
    await observer.emit(Event(type="tool_use", data={"id": "tool-1", "name": "stock"}))
    await observer.emit(
        Event(type="tool_result", data={"id": "tool-1", "content": json.dumps(evidence)})
    )
    await observer.emit(
        Event(
            type="assistant_message",
            data={"text": "Revenue was 120 in 2025 [data](evidence://ev_strict_revenue_2025)."},
        )
    )
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))

    assert observer.citation_repair_requested is True
    assert not any(event.type == "assistant_message" for event in store.appended)
    assert "numeric_unit_missing" in observer.citation_repair_prompt


async def test_strict_policy_audits_source_free_factual_answer() -> None:
    store, _live, observer = _observer_with_strict_policy()

    await observer.emit(
        Event(
            type="assistant_message",
            data={"text": "ROE above 15% is generally considered strong."},
        )
    )
    await observer.emit(
        Event(
            type="session_idle",
            data={"stop_reason": {"type": "end_turn"}, "num_turns": 1},
        )
    )

    assert observer.citation_repair_requested is True
    assert "numeric_claim_without_citation" in observer.citation_repair_prompt
    assert not any(event.type == "assistant_message" for event in store.appended)


async def test_citation_delta_only_draft_is_hidden_and_replaced_after_repair() -> None:
    store, live, observer = _observer_with_citations()
    evidence = {
        "_valuz_evidence": {
            "evidenceHandle": "ev_delta_retry_2025",
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
        Event(type="text_delta", data={"text": "Revenue was 120 USD in 2025."})
    )
    await observer.emit(
        Event(
            type="session_idle",
            data={"stop_reason": {"type": "end_turn"}, "num_turns": 1},
        )
    )

    assert observer.citation_repair_requested is True
    assert not any(event.type in {"text_delta", "assistant_message"} for event in live.events)
    observer.begin_citation_repair()

    await observer.emit(
        Event(
            type="text_delta",
            data={
                "text": _claim_patch_json(
                    observer,
                    replacement_text="Revenue increased.",
                    evidence_handles=["ev_delta_retry_2025"],
                )
            },
        )
    )
    await observer.emit(
        Event(
            type="session_idle",
            data={"stop_reason": {"type": "end_turn"}, "num_turns": 1},
        )
    )

    assistants = [event for event in live.events if event.type == "assistant_message"]
    assert len(assistants) == 1
    assert "120 USD" not in assistants[0].data["text"]
    assert assistants[0].data["text"].startswith("Revenue increased. [1](citation://cit_")
    assert not any(event.type == "text_delta" for event in live.events)
    assert [event.type for event in store.appended].count("assistant_message") == 1


async def test_uncited_claim_without_registered_evidence_requests_retrieval_repair() -> None:
    store, _live, observer = _observer_with_citations()

    await observer.emit(
        Event(type="assistant_message", data={"text": "Revenue was 120 USD in 2025."})
    )
    await observer.emit(
        Event(
            type="session_idle",
            data={"stop_reason": {"type": "end_turn"}, "num_turns": 1},
        )
    )

    assert observer.citation_repair_requested is True
    assert not any(event.type == "assistant_message" for event in store.appended)
    assert '"candidateEvidence":[]' in observer.citation_repair_prompt
    assert "If candidateEvidence is empty, retrieve only evidence needed" in (
        observer.citation_repair_prompt
    )


async def test_second_uncited_answer_publishes_degraded_repair() -> None:
    store, _live, observer = _observer_with_citations()
    evidence = {
        "_valuz_evidence": {
            "evidenceHandle": "ev_retry_2025",
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
    for attempt in range(2):
        await observer.emit(Event(type="assistant_message", data={"text": "Uncited draft."}))
        await observer.emit(
            Event(
                type="session_idle",
                data={"stop_reason": {"type": "end_turn"}, "num_turns": 1},
            )
        )
        if attempt == 0:
            observer.begin_citation_repair()

    assistant = next(event for event in store.appended if event.type == "assistant_message")
    assert assistant.data["text"] == "Uncited draft."
    bundle = assistant.data["citation_bundle"]
    assert bundle["integrity"]["status"] == "degraded"
    assert bundle["integrity"]["repairAttempts"] == 1
    assert bundle["integrity"]["repairOutcome"] == "rejected-protocol-invalid-json"
    assert "publicationBlocked" not in bundle["integrity"]
    # Baseline OSS does not invent a claim-level issue when no claim was
    # detected; the degraded integrity notice remains the UI fallback.
    assert bundle["quality"]["publishStatus"] == "ready"


async def test_repaired_answer_redacts_internal_citation_protocol_prose() -> None:
    store, _live, observer = _observer_with_citations()
    evidence = {
        "_valuz_evidence": {
            "evidenceHandle": "ev_retry_2025",
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
                "quote": "审计意见为无保留意见。",
                "snippet": "审计意见为无保留意见。",
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
        Event(type="assistant_message", data={"text": "营业收入为 100 亿元。"})
    )
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))
    observer.begin_citation_repair()

    await observer.emit(
        Event(
            type="assistant_message",
            data={
                "text": _claim_patch_json(
                    observer,
                    replacement_text="审计意见为无保留意见。",
                    evidence_handles=["ev_retry_2025"],
                )
            },
        )
    )
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))

    assistant = next(event for event in store.appended if event.type == "assistant_message")
    assert "审计意见为无保留意见" in assistant.data["text"]
    assert "citation://cit_" in assistant.data["text"]
    for internal_term in ("嵌套财务子字段", "证据记录", "行内引用", "evidenceHandle"):
        assert internal_term not in assistant.data["text"]


async def test_second_semantically_mismatched_citation_publishes_degraded_repair() -> None:
    store, _live, observer = _observer_with_strict_policy()
    evidence = {
        "_valuz_evidence": {
            "evidenceHandle": "ev_wrong_financial_field",
            "source": {
                "sourceId": "financials",
                "providerId": "data",
                "sourceType": "dataset",
                "title": "Income statement",
                "retrievedAt": "2026-08-01T08:00:00Z",
            },
            "evidence": {
                "kind": "structured-data",
                "datasetId": "financials",
                "toolName": "income_statement",
                "recordKey": "issuer|2025 FY",
                "field": "fiscal_year",
                "value": 2025,
                "period": "2025 FY",
                "capturedAt": "2026-08-01T08:00:00Z",
            },
        }
    }
    await observer.emit(Event(type="tool_use", data={"id": "tool-1", "name": "stock"}))
    await observer.emit(
        Event(type="tool_result", data={"id": "tool-1", "content": json.dumps(evidence)})
    )
    for attempt in range(2):
        await observer.emit(
            Event(
                type="assistant_message",
                data={
                    "text": (
                        "Revenue was 2025 USD in 2025 [data](evidence://ev_wrong_financial_field)."
                    )
                },
            )
        )
        await observer.emit(
            Event(
                type="session_idle",
                data={"stop_reason": {"type": "end_turn"}, "num_turns": 1},
            )
        )
        if attempt == 0:
            assert observer.citation_repair_requested is True
            observer.begin_citation_repair()

    assistant = next(event for event in store.appended if event.type == "assistant_message")
    bundle = assistant.data["citation_bundle"]
    assert assistant.data["text"].startswith("Revenue was 2025 USD in 2025 [1](citation://cit_")
    assert bundle["integrity"]["status"] == "degraded"
    assert bundle["integrity"]["repairAttempts"] == 1
    assert bundle["integrity"]["repairOutcome"] == "rejected-protocol-invalid-json"
    assert "publicationBlocked" not in bundle["integrity"]
    assert bundle["quality"]["publishStatus"] == "draft-only"
    assert "claim_evidence_mismatch" in {issue["code"] for issue in bundle["quality"]["issues"]}


async def test_repair_that_increases_claim_problems_is_rejected_in_favor_of_initial_draft() -> None:
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
    initial = "Revenue increased by 20% [1](evidence://ev_revenue_2025). CEO is Alice."
    await observer.emit(Event(type="assistant_message", data={"text": initial}))
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))
    assert observer.citation_repair_requested is True
    observer.begin_citation_repair()

    await observer.emit(
        Event(
            type="assistant_message",
            data={
                "text": (
                    "Revenue increased by 20% [1](evidence://ev_revenue_2025). "
                    "CEO is Alice. Margin was 42%."
                )
            },
        )
    )
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))

    assistant = next(event for event in store.appended if event.type == "assistant_message")
    assert "Margin was 42%" not in assistant.data["text"]
    assert "CEO is Alice" in assistant.data["text"]
    assert (
        assistant.data["citation_bundle"]["integrity"]["repairOutcome"]
        == "rejected-protocol-invalid-json"
    )


async def test_zero_supported_repair_cannot_replace_partially_supported_initial_draft() -> None:
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
    initial = "Revenue increased by 20% [source](evidence://ev_revenue_2025). CEO is Alice."
    await observer.emit(Event(type="assistant_message", data={"text": initial}))
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))
    assert observer.citation_repair_requested is True
    observer.begin_citation_repair()

    await observer.emit(
        Event(
            type="assistant_message",
            data={"text": "CEO is Alice. Margin was 42%. Demand doubled."},
        )
    )
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))

    assistant = next(event for event in store.appended if event.type == "assistant_message")
    assert "Revenue increased by 20%" in assistant.data["text"]
    assert "Margin was 42%" not in assistant.data["text"]
    assert "资料不足" not in assistant.data["text"]
    assert (
        assistant.data["citation_bundle"]["integrity"]["repairOutcome"]
        == "rejected-protocol-invalid-json"
    )


async def test_repair_cannot_improve_score_by_deleting_all_factual_claims() -> None:
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
    initial = "Revenue increased by 20% [1](evidence://ev_revenue_2025). Margin was 42%."
    await observer.emit(Event(type="assistant_message", data={"text": initial}))
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))
    assert observer.citation_repair_requested is True
    observer.begin_citation_repair()

    await observer.emit(
        Event(
            type="assistant_message",
            data={
                "text": (
                    "部分结果的来源定位不完整，相关内容暂时无法核验。请稍后重试，或以原始资料为准。"
                )
            },
        )
    )
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))

    assistant = next(event for event in store.appended if event.type == "assistant_message")
    assert "Revenue increased by 20%" in assistant.data["text"]
    assert "Margin was 42%" in assistant.data["text"]
    assert (
        assistant.data["citation_bundle"]["integrity"]["repairOutcome"]
        == "rejected-protocol-invalid-json"
    )


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


async def test_large_research_history_does_not_skip_compact_isolated_repair() -> None:
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

    assert observer.citation_repair_requested is True
    assert not any(event.type == "assistant_message" for event in store.appended)
    assert '"originalRequest":"根据文档回答并引用"' in observer.citation_repair_prompt
    assert '"failedDraft":"CEO is Alice."' in observer.citation_repair_prompt
    assert "Untouched answer text" in observer.citation_repair_prompt


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


async def test_advisory_low_tier_issues_do_not_consume_repair_claim_budget() -> None:
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
        is None
    )
    assert [entry["claimId"] for entry in context["claimIssues"]] == [
        "claim_missing_0",
        "claim_missing_1",
    ]
    assert context["generalIssues"] == ["claim_without_citation"]


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
    assert observer._citation_publication_needs_repair(missing_bundle) is True


async def test_repair_context_catalogues_shared_evidence_once() -> None:
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

    assert observer.citation_repair_requested is True
    prompt = observer.citation_repair_prompt
    assert prompt.count('"evidenceHandle":"ev_shared_revenue_2025"') == 1
    assert prompt.count("ev_shared_revenue_2025") >= 2
    assert prompt.count('"evidenceHandle":"ev_shared_revenue_duplicate"') == 1
    assert '"candidateEvidence":[' in prompt


async def test_repair_context_exposes_calculation_input_handles() -> None:
    _store, _live, observer = _observer_with_citations()
    observer._evidence_registry.register_tool_result(
        json.dumps(
            {
                "_valuz_evidence": [
                    {
                        "evidenceHandle": "ev_revenue_12345678",
                        "source": {
                            "sourceId": "income-statement",
                            "providerId": "valuz-stock",
                            "sourceType": "dataset",
                            "title": "Income statement",
                            "retrievedAt": "2026-08-01T10:00:00Z",
                        },
                        "evidence": {
                            "kind": "structured-data",
                            "datasetId": "income-statement",
                            "toolName": "company_income_statement",
                            "field": "operating_revenue",
                            "value": 170899152276,
                            "unit": "CNY",
                            "period": "2024 FY",
                            "capturedAt": "2026-08-01T10:00:00Z",
                        },
                    },
                    {
                        "evidenceHandle": "ev_profit_12345678",
                        "source": {
                            "sourceId": "income-statement",
                            "providerId": "valuz-stock",
                            "sourceType": "dataset",
                            "title": "Income statement",
                            "retrievedAt": "2026-08-01T10:00:00Z",
                        },
                        "evidence": {
                            "kind": "structured-data",
                            "datasetId": "income-statement",
                            "toolName": "company_income_statement",
                            "field": "net_profit_attributable_to_owners_of_the_parent",
                            "value": 86228146422,
                            "unit": "CNY",
                            "period": "2024 FY",
                            "capturedAt": "2026-08-01T10:00:00Z",
                        },
                    },
                    {
                        "evidenceHandle": "ev_calc_12345678",
                        "source": {
                            "sourceId": "calculation",
                            "providerId": "valuz-calculation",
                            "sourceType": "tool-result",
                            "title": "Calculation",
                            "retrievedAt": "2026-08-01T10:00:00Z",
                        },
                        "evidence": {
                            "kind": "calculation",
                            "toolName": "runtime.calculation",
                            "expression": "(profit / revenue) * 100",
                            "result": "50.46",
                            "unit": "%",
                            "calculatedAt": "2026-08-01T10:00:00Z",
                            "inputs": [
                                {
                                    "name": "profit",
                                    "citationId": "ev_profit_12345678",
                                    "value": "86228146422",
                                    "unit": "CNY",
                                },
                                {
                                    "name": "revenue",
                                    "citationId": "ev_revenue_12345678",
                                    "value": "170899152276",
                                    "unit": "CNY",
                                },
                            ],
                        },
                    },
                ]
            }
        ),
        tool_name="citation_calculate",
    )

    prompt = observer._build_citation_repair_prompt(
        {"quality": {"claims": [], "issues": []}},
        "A failed calculation draft.",
    )
    context = json.loads(prompt.split("Restricted repair context (JSON):\n", 1)[1])
    calculation = next(row for row in context["candidateEvidence"] if row["kind"] == "calculation")

    assert calculation["inputs"] == [
        {
            "name": "profit",
            "evidenceHandle": "ev_profit_12345678",
            "value": "86228146422",
            "unit": "CNY",
        },
        {
            "name": "revenue",
            "evidenceHandle": "ev_revenue_12345678",
            "value": "170899152276",
            "unit": "CNY",
        },
    ]


async def test_repair_context_prioritizes_metrics_from_original_request() -> None:
    _store, _live, observer = _observer_with_citations()
    observer._user_prompt = "只列出扣非净利润和商誉金额两个数字。"
    envelopes = []
    for index in range(30):
        quote = f"普通经营说明 {index}。"
        if index == 27:
            quote = "非国际财务报告准则经调整净利润为人民币455,480千元。"
        if index == 29:
            quote = "商譽的賬面值為人民幣3,441,128,000元。"
        envelopes.append(
            {
                "evidenceHandle": f"ev_metric_{index:02d}_12345678",
                "source": {
                    "sourceId": "report-1",
                    "providerId": "valuz-search",
                    "documentId": "report-1",
                    "sourceType": "document",
                    "title": "公司年度财报",
                    "retrievedAt": "2026-08-02T08:00:00Z",
                },
                "evidence": {
                    "kind": "text",
                    "quote": quote,
                    "snippet": quote,
                    "capturedAt": "2026-08-02T08:00:00Z",
                },
            }
        )
    observer._evidence_registry.register_tool_result(
        json.dumps({"_valuz_evidence": envelopes}, ensure_ascii=False),
        tool_name="document_fetch",
    )

    prompt = observer._build_citation_repair_prompt(
        {
            "quality": {
                "claims": [
                    {
                        "claimId": "claim-period",
                        "exact": "报告期为2025年。",
                        "citationRequired": True,
                        "issueCodes": ["claim_evidence_mismatch"],
                        "citationIds": [],
                    }
                ],
                "issues": [{"code": "claim_evidence_mismatch"}],
            }
        },
        "报告期为2025年。",
    )
    context = json.loads(prompt.split("Restricted repair context (JSON):\n", 1)[1])
    handles = {row["evidenceHandle"] for row in context["candidateEvidence"]}

    assert "ev_metric_27_12345678" in handles
    assert "ev_metric_29_12345678" in handles


async def test_repair_context_round_robins_chunks_across_documents() -> None:
    _store, _live, observer = _observer_with_citations()
    envelopes = []
    for document_index in range(4):
        for chunk_index in range(8):
            envelopes.append(
                {
                    "evidenceHandle": (f"ev_doc_{document_index}_chunk_{chunk_index}_12345678"),
                    "source": {
                        "sourceId": f"doc-{document_index}",
                        "providerId": "valuz-search",
                        "documentId": f"doc-{document_index}",
                        "sourceType": "document",
                        "title": f"Quarter {document_index + 1} transcript",
                        "retrievedAt": "2026-08-01T08:00:00Z",
                    },
                    "evidence": {
                        "kind": "text",
                        "quote": f"Quarter {document_index + 1} excerpt {chunk_index}.",
                        "snippet": f"Quarter {document_index + 1} excerpt {chunk_index}.",
                        "capturedAt": "2026-08-01T08:00:00Z",
                    },
                }
            )
    observer._evidence_registry.register_tool_result(
        json.dumps({"_valuz_evidence": envelopes}),
        tool_name="document_fetch",
    )

    prompt = observer._build_citation_repair_prompt(
        {"quality": {"claims": [], "issues": []}},
        "A failed multi-quarter draft.",
    )
    context = json.loads(prompt.split("Restricted repair context (JSON):\n", 1)[1])

    assert len(context["candidateEvidence"]) == 24
    assert {row["documentId"] for row in context["candidateEvidence"]} == {
        "doc-0",
        "doc-1",
        "doc-2",
        "doc-3",
    }


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


async def test_failed_zero_evidence_repair_preserves_a_useful_answer() -> None:
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
    assert observer.citation_repair_requested is True
    observer.begin_citation_repair()
    await observer.emit(
        Event(
            type="assistant_message",
            data={"text": "产品价格仍上涨 300%。"},
        )
    )
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))

    assistant = next(event for event in store.appended if event.type == "assistant_message")
    assert "300%" in assistant.data["text"]
    assert "citation_bundle" in assistant.data
    assert (
        assistant.data["citation_bundle"]["integrity"]["repairOutcome"]
        == "rejected-protocol-invalid-json"
    )


async def test_empty_repair_response_publishes_sealed_baseline_instead_of_blank() -> None:
    store, live, observer = _observer_with_strict_policy()
    await observer.emit(
        Event(
            type="assistant_message",
            data={"text": "营业收入为 120 亿元。"},
        )
    )
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))
    assert observer.citation_repair_requested is True
    observer.begin_citation_repair()

    await observer.emit(Event(type="assistant_message", data={"text": ""}))
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))

    assistant = next(event for event in store.appended if event.type == "assistant_message")
    assert assistant.data["text"] == "营业收入为 120 亿元。"
    assert [event.type for event in live.events].count("assistant_message") == 1
    assert (
        assistant.data["citation_bundle"]["integrity"]["repairOutcome"]
        == "rejected-protocol-invalid-json"
    )


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
