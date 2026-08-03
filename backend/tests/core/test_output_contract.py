from src.core.orchestrator import _citation_output_scope_context
from src.core.output_contract import parse_output_contract


def test_parses_generic_strict_two_field_contract() -> None:
    contract = parse_output_contract("只列出扣非净利润和商誉金额两个数字。")

    assert contract.strict is True
    assert contract.requested_fields == ("扣非净利润", "商誉金额")
    assert contract.requested_item_count == 2
    assert contract.table_only is False
    assert contract.generated_ui_allowed is False
    assert contract.required_fields_for_claim("商誉金额：34.41亿元") == ("商誉金额",)


def test_strict_field_clause_allows_following_period_and_unit_modifier() -> None:
    contract = parse_output_contract(
        "分析海吉亚医疗2025年财报，只列出扣非净利润和商誉金额两个数字，"
        "注明报告期和单位；不要生成图表。"
    )

    assert contract.strict is True
    assert contract.requested_fields == ("扣非净利润", "商誉金额")
    assert contract.requested_item_count == 2
    assert contract.generated_ui_allowed is False
    assert contract.reporting_period_required is True
    assert contract.unit_required is True


def test_parses_uncounted_row_oriented_table_fields_and_formula_requirement() -> None:
    contract = parse_output_contract(
        "请查找贵州茅台 2024 年年报，只用表格列出营业总收入、茅台酒收入、"
        "系列酒收入、直销收入和批发代理收入，并计算各自占比。"
        "注明报告期、单位和计算公式。"
    )

    assert contract.strict is True
    assert contract.table_only is True
    assert contract.requested_fields == (
        "营业总收入",
        "茅台酒收入",
        "系列酒收入",
        "直销收入",
        "批发代理收入",
    )
    assert contract.requested_item_count == 5
    assert contract.reporting_period_required is True
    assert contract.unit_required is True
    assert contract.calculation_formula_required is True


def test_table_layout_instructions_are_not_parsed_as_fields_or_exact_row_count() -> None:
    contract = parse_output_contract(
        "严格只输出一个 Markdown 表格，每家公司一行，并注明各自报告期和单位。"
    )

    assert contract.requested_fields == ()
    assert contract.requested_item_count is None
    assert contract.requested_table_row_count is None
    assert contract.table_only is True


def test_generated_ui_requires_explicit_visual_request() -> None:
    plain = parse_output_contract("列出三家公司的核心产品。")
    chart = parse_output_contract("请生成一个交互式图表展示三家公司的核心产品。")

    assert plain.generated_ui_allowed is False
    assert chart.generated_ui_allowed is True


def test_parses_explicit_two_line_contract() -> None:
    contract = parse_output_contract(
        "请根据年度报告，只用两行列出直销渠道和批发代理渠道的收入。"
    )

    assert contract.strict is True
    assert contract.requested_line_count == 2


def test_parses_multi_period_coverage_contract() -> None:
    zh = parse_output_contract("请总结微软最近四个季度电话会中的表述。")
    zh_published = parse_output_contract("请总结微软最近四个已披露季度的电话会表述。")
    en = parse_output_contract("Summarize Microsoft's last four fiscal quarters.")

    assert zh.requested_period_count == 4
    assert zh_published.requested_period_count == 4
    assert en.requested_period_count == 4
    assert zh.to_dict()["requestedPeriodCount"] == 4
    context = _citation_output_scope_context("请总结微软最近四个季度电话会中的表述。")
    assert "4 distinct period sections" in context
    assert "Do not finish with only" in context


def test_parses_exact_table_rows_and_explicit_columns() -> None:
    contract = parse_output_contract(
        "严格只输出一个 Markdown 表格，恰好 2 行公司数据和 3 列："
        "公司、营业收入、归母净利润；统一使用亿元。"
    )

    assert contract.table_only is True
    assert contract.requested_table_row_count == 2
    assert contract.requested_table_column_count == 3
    assert contract.requested_table_columns == ("公司", "营业收入", "归母净利润")
    assert contract.to_dict()["requestedTableRowCount"] == 2
