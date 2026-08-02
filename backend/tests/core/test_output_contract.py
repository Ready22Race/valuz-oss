from src.core.output_contract import parse_output_contract


def test_parses_generic_strict_two_field_contract() -> None:
    contract = parse_output_contract("只列出扣非净利润和商誉金额两个数字。")

    assert contract.strict is True
    assert contract.requested_fields == ("扣非净利润", "商誉金额")
    assert contract.requested_item_count == 2
    assert contract.table_only is False
    assert contract.generated_ui_allowed is False
    assert contract.required_fields_for_claim("商誉金额：34.41亿元") == ("商誉金额",)


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
