"""Markdown-aware atomic claim extraction and conservative evidence matching."""

from __future__ import annotations

from src.core.claim_audit import (
    MAX_CLAIMS_PER_ANSWER,
    auto_bind_unique_claims,
    extract_claims,
    extract_claims_with_status,
    match_available_evidence,
    verify_evidence_support,
)

_FINANCE_SEMANTICS = {
    "metric_ontology": {
        "metrics": {
            "operating_revenue": {
                "aliases": ["营业收入", "销售收入", "operating revenue"],
                "fields": ["operating_revenue"],
            },
            "net_profit": {
                "aliases": ["净利润", "net profit"],
                "fields": ["net_profit"],
            },
            "revenue_growth": {
                "aliases": ["营业收入同比增长", "revenue growth"],
                "fields": ["operating_revenue_growth_rate"],
            },
            "audit_opinion": {
                "aliases": ["审计意见"],
                "fields": ["audit_opinion_type"],
            },
            "reporting_period": {
                "aliases": ["报告期"],
                "fields": ["fiscal_year"],
            },
            "filing_date": {
                "aliases": ["申报日期"],
                "fields": ["filing_date"],
                "date_role": "publication",
            },
        }
    },
    "unit_ontology": {
        "units": {
            "yuan": {"canonical": "CNY", "aliases": ["元", "CNY"], "scale": 1},
            "ten-thousand": {
                "canonical": "CNY",
                "aliases": ["万元"],
                "scale": 10_000,
            },
            "hundred-million": {
                "canonical": "CNY",
                "aliases": ["亿元"],
                "scale": 100_000_000,
            },
            "percentage": {
                "canonical": "percent",
                "aliases": ["%"],
                "scale": 1,
            },
        }
    },
    "dimensions": {
        "scope": {
            "consolidated": ["合并", "consolidated"],
            "segment": ["分部", "segment"],
        },
        "basis": {},
    },
    "calculation_dependencies": {"revenue_growth": ["operating_revenue"]},
}


def _structured_record(
    handle: str,
    *,
    field: str,
    value: int | float,
    period: str = "2024 FY",
) -> dict:
    return {
        "evidenceHandle": handle,
        "source": {
            "sourceId": f"source-{handle}",
            "providerId": "test-data",
            "sourceType": "dataset",
            "title": "Financial data",
            "retrievedAt": "2026-08-01T08:00:00Z",
        },
        "evidence": {
            "kind": "structured-data",
            "datasetId": "financials",
            "toolName": "company_income_statement",
            "recordKey": "600519|2024 FY",
            "field": field,
            "value": value,
            "unit": "%",
            "period": period,
            "capturedAt": "2026-08-01T08:00:00Z",
        },
    }


def test_extracts_every_claim_when_one_sentence_is_already_cited() -> None:
    claims = extract_claims(
        "Revenue was 120 USD [source](citation://cit_revenue). Margin was 23.5%.",
        mode="required-on-evidence",
    )

    assert [claim.exact for claim in claims] == [
        "Revenue was 120 USD.",
        "Margin was 23.5%.",
    ]
    assert claims[0].attached_citation_ids == ("cit_revenue",)
    assert claims[1].attached_citation_ids == ()
    assert all(claim.citation_required for claim in claims)
    assert {key: claims[0].location[key] for key in ("kind", "blockIndex", "start", "end")} == {
        "kind": "text",
        "blockIndex": 0,
        "start": 0,
        "end": 20,
    }
    assert claims[0].location["sourceStart"] == 0
    assert claims[0].location["sourceEnd"] > claims[0].location["end"]


def test_attaches_citation_written_after_terminal_punctuation() -> None:
    claims = extract_claims(
        "营业总收入为 174,144,069,958.25 元。 [来源](citation://cit_revenue) 下一项为说明。",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )

    assert claims[0].exact == "营业总收入为 174,144,069,958.25 元。"
    assert claims[0].attached_citation_ids == ("cit_revenue",)
    assert claims[1].attached_citation_ids == ()


def test_extracts_dates_and_non_numeric_facts_but_not_reasoning() -> None:
    claims = extract_claims(
        "The company was founded in 1999. Alice is the CEO. This may improve execution.",
        mode="required-on-evidence",
    )

    by_exact = {claim.exact: claim for claim in claims}
    assert by_exact["The company was founded in 1999."].kind == "date-fact"
    assert by_exact["The company was founded in 1999."].citation_required is True
    assert by_exact["Alice is the CEO."].kind == "document-claim"
    assert by_exact["Alice is the CEO."].citation_required is True
    assert by_exact["This may improve execution."].kind == "reasoning"
    assert by_exact["This may improve execution."].citation_required is False


def test_strict_domain_allows_explicit_empty_search_result_without_citation() -> None:
    not_found = extract_claims(
        "未找到符合条件的相关资料。",
        mode="strict-domain",
    )
    english_not_found = extract_claims(
        "No matching documents were found.",
        mode="strict-domain",
    )
    mixed = extract_claims(
        "未找到相关资料，但公司成立于 1999 年。",
        mode="strict-domain",
    )

    assert not_found == []
    assert len(english_not_found) == 1
    assert english_not_found[0].citation_required is False
    assert len(mixed) == 1
    assert mixed[0].kind == "date-fact"
    assert mixed[0].citation_required is True


def test_strict_domain_does_not_flag_section_titles_or_user_facing_limitations() -> None:
    claims = extract_claims(
        "**2. 营业总收入 与 营业收入**\n\n"
        "贵州茅台 2024 年度三项查询结果如下：\n\n"
        "部分结果的来源定位不完整，相关内容暂时无法核验。\n\n"
        "如需进一步确认，建议查阅贵州茅台 2024 年年度报告原文。",
        mode="strict-domain",
    )

    assert [claim.exact for claim in claims] == [
        "贵州茅台 2024 年度三项查询结果如下：",
        "如需进一步确认，建议查阅贵州茅台 2024 年年度报告原文。",
    ]
    assert [claim.kind for claim in claims] == ["presentation", "reasoning"]
    assert all(claim.citation_required is False for claim in claims)


def test_independently_cited_comma_clauses_are_atomic_claims() -> None:
    claims = extract_claims(
        "2024 年度审计意见为无保留意见 [a](citation://cit_a)，"
        "报告期为全年 [b](citation://cit_b)，"
        "申报日期为 2025-04-03 [c](citation://cit_c)。",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )

    assert [claim.attached_citation_ids for claim in claims] == [
        ("cit_a",),
        ("cit_b",),
        ("cit_c",),
    ]
    assert [claim.normalized.get("period") for claim in claims] == [
        "2024 FY",
        "2024 FY",
        "2024 FY",
    ]


def test_each_comma_clause_keeps_its_explicit_period_and_infers_derived_metric() -> None:
    claims = extract_claims(
        "2024 年营业收入为 1,708.99 亿元 [a](citation://cit_a)，"
        "2023 年营业收入为 1,476.94 亿元 [b](citation://cit_b)，"
        "2024 年同比增速为 15.71% [c](citation://cit_c)。",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )

    assert [claim.normalized.get("period") for claim in claims] == [
        "2024 FY",
        "2023 FY",
        "2024 FY",
    ]
    assert [claim.normalized.get("metric") for claim in claims] == [
        "operating_revenue",
        "operating_revenue",
        "revenue_growth",
    ]


def test_citation_clause_split_ignores_internal_commas_before_the_binding() -> None:
    claims = extract_claims(
        "2024 年营业收入为 170,899,152,276.34 元，较上年同期增长 15.71% "
        "[表格](citation://cit_table)，利润表对此亦予以披露 "
        "[分析](citation://cit_analysis)。",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )

    assert [claim.attached_citation_ids for claim in claims] == [
        ("cit_table",),
    ]
    assert claims[0].exact == ("2024 年营业收入为 170,899,152,276.34 元，较上年同期增长 15.71%，")


def test_text_evidence_supports_scaled_financial_values_and_percentages() -> None:
    claim = extract_claims(
        "2024 年营业收入为 1,708.99 亿元，同比增长 15.71%。",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )[0]
    evidence = {
        "kind": "text",
        "quote": ("营业收入 | 170,899,000,000 元 | 2024 年；本期比上年同期增长 15.71%。"),
    }

    assert (
        verify_evidence_support(claim, evidence, semantics=_FINANCE_SEMANTICS).status == "supported"
    )


def test_list_and_table_claims_have_stable_structural_locations() -> None:
    answer = """# Summary

- Revenue was 120 USD.
- Revenue was 120 USD.

| Metric | 2024 |
|---|---:|
| Revenue | 120 USD |
| Profit | 20 USD |

```python
year = 2024
```
"""

    first = extract_claims(answer, mode="strict-domain")
    second = extract_claims(answer, mode="strict-domain")

    assert [claim.claim_id for claim in first] == [claim.claim_id for claim in second]
    repeated = [claim for claim in first if claim.exact == "Revenue was 120 USD."]
    assert len(repeated) == 2
    assert repeated[0].claim_id != repeated[1].claim_id
    assert repeated[0].location["kind"] == "list-item"
    assert repeated[0].location["itemIndex"] == 0
    assert repeated[1].location["itemIndex"] == 1
    table_claims = [claim for claim in first if claim.location["kind"] == "table-cell"]
    assert [claim.exact for claim in table_claims] == [
        "Revenue — 2024: 120 USD",
        "Profit — 2024: 20 USD",
    ]
    assert {
        key: table_claims[0].location[key]
        for key in ("kind", "blockIndex", "rowIndex", "columnIndex")
    } == {
        "kind": "table-cell",
        "blockIndex": 1,
        "rowIndex": 0,
        "columnIndex": 1,
    }
    assert table_claims[0].location["sourceEnd"] > table_claims[0].location["sourceStart"]
    assert all("year = 2024" not in claim.exact for claim in first)


def test_claims_inherit_period_context_from_markdown_headings() -> None:
    answer = """# 贵州茅台

## 2024 年

- 营业收入为 1,708.99 亿元。

| 指标 | 数值 |
|---|---:|
| 净利润 | 862.28 亿元 |
"""

    claims = extract_claims(answer, mode="strict-domain", semantics=_FINANCE_SEMANTICS)

    assert [claim.exact for claim in claims] == [
        "营业收入为 1,708.99 亿元。",
        "净利润 — 数值: 862.28 亿元",
    ]
    assert [claim.normalized["period"] for claim in claims] == ["2024 FY", "2024 FY"]
    revenue = _structured_record(
        "ev_heading_revenue_12345678",
        field="operating_revenue",
        value=170_899_000_000,
    )
    revenue["evidence"]["unit"] = "CNY"
    revenue["evidence"]["entityName"] = "贵州茅台"

    assert (
        match_available_evidence(
            claims[0],
            [revenue],
            semantics=_FINANCE_SEMANTICS,
        ).status
        == "exact"
    )

    without_heading = extract_claims(
        "营业收入为 1,708.99 亿元。",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )[0]
    assert (
        verify_evidence_support(
            without_heading,
            revenue["evidence"],
            semantics=_FINANCE_SEMANTICS,
        ).status
        == "partially-supported"
    )


def test_markdown_sources_heading_stops_claim_audit() -> None:
    claims = extract_claims(
        "Revenue was 120 USD.\n\n## Sources\n\n- Publisher is Example Corp.",
        mode="strict-domain",
    )

    assert [claim.exact for claim in claims] == ["Revenue was 120 USD."]


def test_short_english_change_statements_are_claims() -> None:
    claims = extract_claims("- Revenue grew.\n- Profit fell.", mode="strict-domain")

    assert [claim.exact for claim in claims] == ["Revenue grew.", "Profit fell."]
    assert all(claim.citation_required for claim in claims)


def test_claim_extraction_is_bounded_and_reports_truncation() -> None:
    answer = "\n".join(
        f"- Company {index} reported revenue of {index + 1} USD."
        for index in range(MAX_CLAIMS_PER_ANSWER + 5)
    )

    claims, truncated = extract_claims_with_status(answer, mode="strict-domain")

    assert len(claims) == MAX_CLAIMS_PER_ANSWER
    assert truncated is True


def test_matcher_auto_binds_only_one_semantically_exact_candidate() -> None:
    claim = extract_claims(
        "Gross margin was 23.5% in 2024.",
        mode="strict-domain",
    )[0]
    exact = _structured_record(
        "ev_margin_12345678",
        field="gross_margin",
        value=23.5,
    )
    wrong_field = _structured_record(
        "ev_tax_12345678",
        field="tax_rate",
        value=23.5,
    )

    result = match_available_evidence(claim, [exact, wrong_field])

    assert result.status == "exact"
    assert result.handles == ("ev_margin_12345678",)


def test_matcher_never_guesses_between_equally_exact_candidates() -> None:
    claim = extract_claims(
        "Gross margin was 23.5% in 2024.",
        mode="strict-domain",
    )[0]
    first = _structured_record(
        "ev_margin_first_12345678",
        field="gross_margin",
        value=23.5,
    )
    second = _structured_record(
        "ev_margin_second_12345678",
        field="gross_margin",
        value=23.5,
    )

    result = match_available_evidence(claim, [first, second])

    assert result.status == "ambiguous"
    assert result.handles == (
        "ev_margin_first_12345678",
        "ev_margin_second_12345678",
    )


def test_matcher_does_not_report_different_scopes_as_same_point_conflict() -> None:
    claim = extract_claims(
        "Gross margin was 23.5% in 2024.",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )[0]
    consolidated = _structured_record(
        "ev_margin_consolidated_12345678",
        field="gross_margin",
        value=23.5,
    )
    consolidated["evidence"]["scope"] = "consolidated"
    segment = _structured_record(
        "ev_margin_segment_12345678",
        field="gross_margin",
        value=25.0,
    )
    segment["evidence"]["scope"] = "segment"

    result = match_available_evidence(
        claim,
        [consolidated, segment],
        semantics=_FINANCE_SEMANTICS,
    )

    assert result.status == "exact"
    assert result.handles == ("ev_margin_consolidated_12345678",)


def test_finance_semantics_match_metric_period_and_scaled_unit() -> None:
    claim = extract_claims(
        "贵州茅台（600519）2024 年营业收入为 1,741.44 亿元。",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )[0]
    correct = _structured_record(
        "ev_revenue_12345678",
        field="operating_revenue",
        value=174_144_000_000,
    )
    correct["evidence"].update({"unit": "CNY", "entityId": "600519", "period": "2024 FY"})
    wrong_metric = _structured_record(
        "ev_profit_12345678",
        field="net_profit",
        value=174_144_000_000,
    )
    wrong_metric["evidence"].update({"unit": "CNY", "entityId": "600519", "period": "2024 FY"})
    broader_metric = _structured_record(
        "ev_total_revenue_12345678",
        field="total_revenue",
        value=174_144_000_000,
    )
    broader_metric["evidence"].update(
        {
            "metric": "total_revenue",
            "unit": "CNY",
            "entityId": "600519",
            "period": "2024 FY",
        }
    )

    result = match_available_evidence(
        claim,
        [correct, wrong_metric, broader_metric],
        semantics=_FINANCE_SEMANTICS,
    )

    assert claim.normalized == {
        "value": "1741.44",
        "unit": "亿元",
        "valueBase": "174144000000",
        "unitBase": "CNY",
        "period": "2024 FY",
        "metric": "operating_revenue",
    }
    assert result.status == "exact"
    assert result.handles == ("ev_revenue_12345678",)


def test_structured_evidence_with_a_different_ticker_is_contradicted() -> None:
    claim = extract_claims(
        "000858 在 2024 年营业收入为 1,708.99 亿元。",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )[0]
    evidence = _structured_record(
        "ev_wrong_entity_12345678",
        field="operating_revenue",
        value=170_899_000_000,
    )
    evidence["evidence"].update({"unit": "CNY", "entityId": "600519"})

    assert (
        verify_evidence_support(
            claim,
            evidence["evidence"],
            semantics=_FINANCE_SEMANTICS,
        ).status
        == "contradicted"
    )


def test_plain_six_digit_value_is_not_mistaken_for_a_ticker() -> None:
    claim = extract_claims(
        "Employee count was 123456 in 2024.",
        mode="strict-domain",
    )[0]
    evidence = _structured_record(
        "ev_employee_count_12345678",
        field="employee_count",
        value=123456,
    )
    evidence["evidence"].update({"unit": "", "entityId": "600519", "entityName": "Kweichow"})

    assert verify_evidence_support(claim, evidence["evidence"]).status == "partially-supported"


def test_finance_semantics_accept_display_rounding_but_not_wrong_value() -> None:
    claim = extract_claims(
        "贵州茅台（600519）2024 年营业收入为 1,708.99 亿元。",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )[0]
    rounded = _structured_record(
        "ev_revenue_rounded_12345678",
        field="total_revenue.operating_revenue",
        value=170_899_152_276,
    )
    rounded["evidence"].update(
        {
            "metric": "operating_revenue",
            "unit": "CNY",
            "entityId": "600519",
            "period": "2024 FY",
        }
    )
    wrong = _structured_record(
        "ev_revenue_wrong_12345678",
        field="total_revenue.operating_revenue",
        value=170_798_000_000,
    )
    wrong["evidence"].update(
        {
            "metric": "operating_revenue",
            "unit": "CNY",
            "entityId": "600519",
            "period": "2024 FY",
        }
    )

    assert (
        verify_evidence_support(
            claim,
            rounded,
            semantics=_FINANCE_SEMANTICS,
        ).status
        == "supported"
    )
    assert (
        verify_evidence_support(
            claim,
            wrong,
            semantics=_FINANCE_SEMANTICS,
        ).status
        == "not-found"
    )


def test_reportify_nine_month_period_matches_chinese_ytd_claim() -> None:
    claim = extract_claims(
        "贵州茅台（600519）2024 年前三季度营业收入为 1,000 亿元。",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )[0]
    evidence = _structured_record(
        "ev_q3_ytd_revenue_12345678",
        field="operating_revenue",
        value=100_000_000_000,
        period="2024 Q3 (9 months)",
    )
    evidence["evidence"].update({"unit": "CNY", "entityId": "600519"})

    assert claim.normalized["period"] == "2024 Q3 YTD"
    assert (
        match_available_evidence(
            claim,
            [evidence],
            semantics=_FINANCE_SEMANTICS,
        ).status
        == "exact"
    )


def test_calculation_without_metric_does_not_match_empty_chinese_alias_token() -> None:
    claim = extract_claims(
        "growth was 20%.",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )[0]
    calculation = {
        "kind": "calculation",
        "expression": "((current / prior) - 1) * 100",
        "inputs": [],
        "result": 20,
        "unit": "%",
        "calculatedAt": "2026-08-01T08:00:00Z",
    }

    assert (
        verify_evidence_support(claim, calculation, semantics=_FINANCE_SEMANTICS).status
        == "supported"
    )


def test_finance_semantics_split_independent_metric_clauses() -> None:
    claims = extract_claims(
        "2024 年营业收入为 1 亿元，净利润为 2 亿元。",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )

    assert [claim.exact for claim in claims] == [
        "2024 年营业收入为 1 亿元，",
        "净利润为 2 亿元。",
    ]
    assert [claim.normalized["metric"] for claim in claims] == [
        "operating_revenue",
        "net_profit",
    ]


def test_text_quote_matching_ignores_pdf_line_wrap_spacing_in_chinese() -> None:
    claim = extract_claims(
        "我们认为，财务报表公允反映了贵州茅台公司2024年度的经营成果。",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )[0]
    evidence = {
        "kind": "text",
        "quote": "我们认为，财务报表公允反映了贵州茅台公司2024\n年度的经营成果。",
    }

    assert (
        verify_evidence_support(claim, evidence, semantics=_FINANCE_SEMANTICS).status == "supported"
    )


def test_text_quote_matching_allows_attribution_before_exact_quote() -> None:
    claim = extract_claims(
        (
            '年报重要提示第三条原文："天健会计师事务所(特殊普通合伙)'
            '为本公司出具了标准无保留意见的审计报告。"'
        ),
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )[0]
    evidence = {
        "kind": "text",
        "quote": ("三、 天健会计师事务所(特殊普通合伙)为本公司出具了标准无保留意见的审计报告。"),
    }

    assert (
        verify_evidence_support(claim, evidence, semantics=_FINANCE_SEMANTICS).status == "supported"
    )


def test_text_numeric_support_accepts_equivalent_currency_conversions() -> None:
    claim = extract_claims(
        ("2024年度营业收入为17,089,915.23万元，即170,899,152,300元，约1,708.99亿元。"),
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )[0]
    evidence = {
        "kind": "text",
        "quote": "2024 年度，营业收入为人民币 17,089,915.23 万元。",
    }

    assert (
        verify_evidence_support(claim, evidence, semantics=_FINANCE_SEMANTICS).status == "supported"
    )


def test_text_table_evidence_supports_a_claim_with_an_equivalent_display_value() -> None:
    claim = extract_claims(
        ("直销渠道：2024年本期销售收入 74,843,327,030.79 元（约 748.43 亿元），同比增长 11.32%。"),
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )[0]
    evidence = {
        "kind": "text",
        "quote": ("| 按销售渠道 | 金额 | 同比 |\n| 直销 | 74,843,327,030.79 | 11.32 |"),
        "prefix": "渠道类型 本期销售收入 上期销售收入 本期销售量 上期销售量",
    }

    assert (
        verify_evidence_support(claim, evidence, semantics=_FINANCE_SEMANTICS).status == "supported"
    )


def test_generic_market_quote_with_all_numbers_is_supported_without_metric_ontology() -> None:
    claim = extract_claims(
        (
            "据 TrendForce 数据，2026年Q2通用DRAM合约价环比Q1上涨 58–63%，"
            "NAND Flash合约价上涨 81–86%。"
        ),
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )[0]
    evidence = {
        "kind": "text",
        "quote": (
            "TrendForce：2026 年第二季（Q2）通用 DRAM 合约价预计较第一季（Q1）"
            "上涨 58–63%；NAND Flash 合约价预计上涨\n81–86%。"
        ),
    }

    assert (
        verify_evidence_support(claim, evidence, semantics=_FINANCE_SEMANTICS).status == "supported"
    )


def test_generic_numeric_quote_does_not_match_unrelated_subject_with_same_values() -> None:
    claim = extract_claims(
        "DRAM 合约价上涨 58–63%。",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )[0]
    evidence = {
        "kind": "text",
        "quote": "航空客运量同比上涨 58–63%。",
    }

    assert (
        verify_evidence_support(claim, evidence, semantics=_FINANCE_SEMANTICS).status != "supported"
    )


def test_finance_clause_split_ignores_numeric_thousands_separator() -> None:
    claims = extract_claims(
        ("贵州茅台（600519）2024 年营业收入为 1,708.99 亿元，归母净利润为 862.28 亿元。"),
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )

    assert [claim.exact for claim in claims] == [
        "贵州茅台（600519）2024 年营业收入为 1,708.99 亿元，",
        "归母净利润为 862.28 亿元。",
    ]
    assert [claim.normalized["metric"] for claim in claims] == [
        "operating_revenue",
        "net_profit",
    ]


def test_auto_bind_keeps_each_citation_before_its_clause_punctuation() -> None:
    revenue = _structured_record(
        "ev_revenue_clause_12345678",
        field="operating_revenue",
        value=170_899_152_276,
    )
    revenue["evidence"].update({"unit": "CNY", "period": "2024 FY"})
    profit = _structured_record(
        "ev_profit_clause_12345678",
        field="net_profit",
        value=89_334_728_026,
    )
    profit["evidence"].update({"unit": "CNY", "period": "2024 FY"})

    result = auto_bind_unique_claims(
        "2024 年营业收入为 1,708.99 亿元，净利润为 893.35 亿元。",
        [revenue, profit],
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )
    rebound = extract_claims(
        result.text,
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )

    assert result.text.count("evidence://") == 2
    assert [claim.attached_evidence_handles for claim in rebound] == [
        ("ev_revenue_clause_12345678",),
        ("ev_profit_clause_12345678",),
    ]
