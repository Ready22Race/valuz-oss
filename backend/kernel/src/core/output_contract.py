"""Deterministic output-scope contract parsed from the user request."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_STRICT_RE = re.compile(
    r"(?:只列出|只输出|只返回|只展示|只给出|仅列出|仅输出|仅返回|"
    r"只用\s*[一二两三四五六七八九十\d]+\s*行|"
    r"(?:只|仅)\s*(?:用|以)\s*(?:Markdown\s*)?表格\s*(?:列出|输出|返回|展示)|"
    r"不要添加其他内容|\bonly\s+(?:list|output|return|show)\b|"
    r"\bnothing\s+else\b)",
    re.IGNORECASE,
)
_LINE_COUNT_RE = re.compile(
    r"(?:只用|仅用|严格)\s*(?P<count>[一二两三四五六七八九十\d]+)\s*行",
    re.IGNORECASE,
)
_ZH_FIELDS_RE = re.compile(
    r"(?:只|仅)(?:需要|要|列出|输出|返回|展示|给出)?\s*"
    r"(?P<fields>[^。；\n]{2,180}?)"
    r"(?P<count>[一二两三四五六七八九十\d]+)个"
    r"(?:数字|字段|指标|项目|项)(?=[，,。；;\n]|$)",
    re.IGNORECASE,
)
_ZH_UNCOUNTED_FIELDS_RE = re.compile(
    r"(?:只|仅)\s*(?:(?:用|以)\s*(?:Markdown\s*)?表格\s*)?"
    r"(?:列出|输出|返回|展示|给出)\s*"
    r"(?P<fields>[^。；\n]{2,240}?)"
    r"(?=(?:[，,]\s*)?(?:并(?:计算|注明|标明|说明)|注明|标明|说明|"
    r"不要|不得|禁止)|[。；\n]|$)",
    re.IGNORECASE,
)
_UNCOUNTED_OUTPUT_INSTRUCTION_RE = re.compile(
    r"(?:Markdown\s*)?表格|每(?:家|个)?(?:公司|企业|实体)?\s*[一二两三四五六七八九十\d]+\s*行|"
    r"(?:并|且|同时)?\s*(?:列出|输出|返回|展示|说明)\s*每(?:个|项|条)?|"
    r"\b(?:markdown\s+table|rows?\s+per\s+(?:company|entity))\b",
    re.IGNORECASE,
)
_EN_FIELDS_RE = re.compile(
    r"\bonly\s+(?:list|output|return|show)\s+(?P<fields>[^.\n]{2,180}?)"
    r"(?:\s+and\s+nothing\s+else)?(?:[.\n]|$)",
    re.IGNORECASE,
)
_ZH_EXACT_ITEM_COUNT_RE = re.compile(
    r"(?:推荐|筛选|挑选|选出|列出|给出|提供)\s*"
    r"(?:恰好|正好|严格)?\s*"
    r"(?P<count>[一二两三四五六七八九十\d]+)\s*"
    r"(?:家|个|项|条|种|份|位)(?:公司|企业|股票|案例|方案|策略|产品|标的)?",
    re.IGNORECASE,
)
_EN_EXACT_ITEM_COUNT_RE = re.compile(
    r"\b(?:recommend|list|select|choose|provide|give(?:\s+me)?)\s+"
    r"(?:exactly\s+)?"
    r"(?P<count>one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+"
    r"(?:[A-Za-z][A-Za-z-]*\s+){0,3}"
    r"(?:items?|companies|stocks?|cases?|options?|strateg(?:y|ies)|products?|names?)\b",
    re.IGNORECASE,
)
_APPROXIMATE_COUNT_PREFIX_RE = re.compile(
    r"(?:至少|不少于|最多|至多|约|大约|不超过|多于|少于)\s*$|"
    r"\b(?:at\s+least|no\s+fewer\s+than|up\s+to|at\s+most|about|approximately|"
    r"more\s+than|fewer\s+than)\s*$",
    re.IGNORECASE,
)
_FIELD_SPLIT_RE = re.compile(r"\s*(?:、|，|,|/|以及|及|与|和|\band\b)\s*", re.IGNORECASE)
_TABLE_ONLY_RE = re.compile(
    r"(?:只|仅).{0,16}(?:输出|返回|列出).{0,16}(?:Markdown\s*)?表格|"
    r"(?:只|仅)\s*(?:用|以)\s*(?:Markdown\s*)?表格\s*(?:列出|输出|返回|展示)|"
    r"\bonly\s+(?:output|return)\b.{0,24}\bmarkdown\s+table\b",
    re.IGNORECASE,
)
_ZH_TABLE_COLUMNS_RE = re.compile(
    r"(?P<count>[一二两三四五六七八九十\d]+)\s*列\s*[:：]\s*"
    r"(?P<columns>[^。；;\n]{1,180})",
    re.IGNORECASE,
)
_EN_TABLE_COLUMNS_RE = re.compile(
    r"(?:(?:exactly|only)\s+)?(?P<count>one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+"
    r"columns?\s*[:：]\s*(?P<columns>[^.;\n]{1,180})",
    re.IGNORECASE,
)
_ZH_TABLE_ROW_COUNT_RE = re.compile(
    r"(?<!每家公司)(?<!每个公司)(?<!每个企业)(?<!每个实体)"
    r"(?:恰好|严格|只(?:要|有|输出)?|仅(?:要|有|输出)?)?\s*"
    r"(?P<count>[一二两三四五六七八九十\d]+)\s*行"
    r"(?:公司|企业|表格)?(?:数据|记录|结果)?",
    re.IGNORECASE,
)
_EN_TABLE_ROW_COUNT_RE = re.compile(
    r"(?:(?:exactly|only)\s+)(?P<count>one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+"
    r"(?:data\s+)?rows?\b",
    re.IGNORECASE,
)
_GENERATED_UI_RE = re.compile(
    r"(?:图表|仪表盘|可视化|交互(?:页面|界面)|dashboard|chart|visuali[sz]ation|"
    r"interactive\s+(?:ui|interface))",
    re.IGNORECASE,
)
_NEGATED_GENERATED_UI_RE = re.compile(
    r"(?:不要|无需|无须|不必|禁止|不需要).{0,12}"
    r"(?:生成|创建|添加|展示)?(?:任何)?\s*"
    r"(?:图表|仪表盘|可视化|交互(?:页面|界面)|dashboard|chart|visuali[sz]ation)|"
    r"\b(?:do not|don't|without|no need to)\b.{0,20}"
    r"(?:dashboard|chart|visuali[sz]ation|interactive\s+(?:ui|interface))",
    re.IGNORECASE,
)
_NEGATED_METADATA_RE = re.compile(
    r"(?:不要|无需|无须|不必|禁止|不需要).{0,12}"
    r"(?:注明|标明|列明|说明|附上|显示|提供)?[^。；;\n]{0,20}"
    r"(?:报告期|期间|财年|单位)|"
    r"\b(?:do\s+not|don't|without|no\s+need\s+to)\b.{0,24}"
    r"(?:reporting\s+period|fiscal\s+(?:year|period)|unit)",
    re.IGNORECASE,
)
_REPORTING_PERIOD_METADATA_RE = re.compile(
    r"(?:注明|标明|列明|说明|附上|显示|提供)[^。；;\n]{0,32}"
    r"(?:报告期|期间|财年)|"
    r"\b(?:indicate|show|include|state|provide)\b[^.;\n]{0,32}"
    r"(?:reporting\s+period|fiscal\s+(?:year|period))",
    re.IGNORECASE,
)
_UNIT_METADATA_RE = re.compile(
    r"(?:注明|标明|列明|说明|附上|显示|提供)[^。；;\n]{0,32}单位|"
    r"\b(?:indicate|show|include|state|provide)\b[^.;\n]{0,32}\bunit\b",
    re.IGNORECASE,
)
_CALCULATION_FORMULA_RE = re.compile(
    r"(?:注明|标明|列明|说明|附上|显示|提供|列出|给出)[^。；;\n]{0,32}"
    r"(?:计算)?公式|"
    r"\b(?:indicate|show|include|state|provide|list|give)\b[^.;\n]{0,32}"
    r"\bformula\b",
    re.IGNORECASE,
)
_ZH_PERIOD_COUNT_RE = re.compile(
    r"(?:最近|近|过去|此前|前)\s*(?P<count>[一二两三四五六七八九十\d]+)\s*个?"
    r"\s*(?:(?:已(?:披露|发布|公布|公开)|公开(?:披露|发布)|连续|完整|可得|可获取)\s*)?"
    r"(?:季度|财季|报告期)",
    re.IGNORECASE,
)
_EN_PERIOD_COUNT_RE = re.compile(
    r"\b(?:last|recent|previous|past)\s+"
    r"(?P<count>one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+"
    r"(?:fiscal\s+)?(?:quarters?|periods?)\b",
    re.IGNORECASE,
)
_ZH_NUMBERS = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}
_EN_NUMBERS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}


@dataclass(frozen=True)
class OutputContract:
    strict: bool
    requested_fields: tuple[str, ...] = ()
    requested_item_count: int | None = None
    requested_result_count: int | None = None
    requested_line_count: int | None = None
    requested_period_count: int | None = None
    requested_table_columns: tuple[str, ...] = ()
    requested_table_column_count: int | None = None
    requested_table_row_count: int | None = None
    table_only: bool = False
    generated_ui_allowed: bool = False
    reporting_period_required: bool = False
    unit_required: bool = False
    calculation_formula_required: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "strict": self.strict,
            "requestedFields": list(self.requested_fields),
            "requestedItemCount": self.requested_item_count,
            "requestedResultCount": self.requested_result_count,
            "requestedLineCount": self.requested_line_count,
            "requestedPeriodCount": self.requested_period_count,
            "requestedTableColumns": list(self.requested_table_columns),
            "requestedTableColumnCount": self.requested_table_column_count,
            "requestedTableRowCount": self.requested_table_row_count,
            "tableOnly": self.table_only,
            "generatedUiAllowed": self.generated_ui_allowed,
            "reportingPeriodRequired": self.reporting_period_required,
            "unitRequired": self.unit_required,
            "calculationFormulaRequired": self.calculation_formula_required,
        }

    def required_fields_for_claim(self, exact: str) -> tuple[str, ...]:
        folded = _fold(exact)
        return tuple(field for field in self.requested_fields if _fold(field) in folded)


def parse_output_contract(user_prompt: str) -> OutputContract:
    requested_fields: tuple[str, ...] = ()
    requested_count: int | None = None
    match = _ZH_FIELDS_RE.search(user_prompt)
    if match is not None:
        requested_fields = _split_fields(match.group("fields"))
        requested_count = _parse_count(match.group("count"))
    else:
        english = _EN_FIELDS_RE.search(user_prompt)
        if english is not None:
            requested_fields = _split_fields(english.group("fields"))
            requested_count = len(requested_fields) or None
        else:
            uncounted = _ZH_UNCOUNTED_FIELDS_RE.search(user_prompt)
            if uncounted is not None:
                raw_fields = uncounted.group("fields")
                parsed_fields = _split_fields(raw_fields)
                # Uncounted lists are accepted only when punctuation makes a
                # field enumeration explicit.  This prevents phrases such as
                # "只输出 Markdown 表格" or a dimension clause from becoming
                # fabricated metric names.
                if (
                    len(parsed_fields) >= 2
                    and re.search(r"[、，,/]", raw_fields)
                    and not any(
                        _UNCOUNTED_OUTPUT_INSTRUCTION_RE.search(field)
                        for field in parsed_fields
                    )
                ):
                    requested_fields = parsed_fields
                    requested_count = len(requested_fields)
    requested_result_count = None
    if requested_count is None:
        requested_result_count = _parse_explicit_item_count(user_prompt)
        requested_count = requested_result_count
    if requested_count is not None and len(requested_fields) > requested_count:
        requested_fields = requested_fields[-requested_count:]
    zh_period = _ZH_PERIOD_COUNT_RE.search(user_prompt)
    en_period = _EN_PERIOD_COUNT_RE.search(user_prompt) if zh_period is None else None
    requested_period_count = None
    if zh_period is not None:
        requested_period_count = _parse_count(zh_period.group("count"))
    elif en_period is not None:
        raw_count = en_period.group("count").lower()
        requested_period_count = (
            int(raw_count) if raw_count.isdigit() else _EN_NUMBERS.get(raw_count)
        )
    generated_ui_prompt = _NEGATED_GENERATED_UI_RE.sub("", user_prompt)
    metadata_prompt = _NEGATED_METADATA_RE.sub("", user_prompt)
    table_columns_match = _ZH_TABLE_COLUMNS_RE.search(user_prompt)
    if table_columns_match is not None:
        requested_table_columns = _split_fields(table_columns_match.group("columns"))
        requested_table_column_count = _parse_count(table_columns_match.group("count"))
    else:
        english_columns = _EN_TABLE_COLUMNS_RE.search(user_prompt)
        if english_columns is not None:
            requested_table_columns = _split_fields(english_columns.group("columns"))
            raw_count = english_columns.group("count").lower()
            requested_table_column_count = (
                int(raw_count) if raw_count.isdigit() else _EN_NUMBERS.get(raw_count)
            )
        else:
            requested_table_columns = ()
            requested_table_column_count = None
    table_row_match = _ZH_TABLE_ROW_COUNT_RE.search(user_prompt)
    english_rows = _EN_TABLE_ROW_COUNT_RE.search(user_prompt) if table_row_match is None else None
    if table_row_match is not None:
        requested_table_row_count = _parse_count(table_row_match.group("count"))
    elif english_rows is not None:
        raw_count = english_rows.group("count").lower()
        requested_table_row_count = (
            int(raw_count) if raw_count.isdigit() else _EN_NUMBERS.get(raw_count)
        )
    else:
        requested_table_row_count = None
    return OutputContract(
        strict=bool(_STRICT_RE.search(user_prompt)),
        requested_fields=requested_fields,
        requested_item_count=requested_count,
        requested_result_count=requested_result_count,
        requested_line_count=(
            _parse_count(line_match.group("count"))
            if (line_match := _LINE_COUNT_RE.search(user_prompt)) is not None
            else None
        ),
        requested_period_count=requested_period_count,
        requested_table_columns=requested_table_columns,
        requested_table_column_count=requested_table_column_count,
        requested_table_row_count=requested_table_row_count,
        table_only=bool(_TABLE_ONLY_RE.search(user_prompt)),
        generated_ui_allowed=bool(_GENERATED_UI_RE.search(generated_ui_prompt)),
        reporting_period_required=bool(_REPORTING_PERIOD_METADATA_RE.search(metadata_prompt)),
        unit_required=bool(_UNIT_METADATA_RE.search(metadata_prompt)),
        calculation_formula_required=bool(_CALCULATION_FORMULA_RE.search(user_prompt)),
    )


def _split_fields(value: str) -> tuple[str, ...]:
    fields = []
    for raw in _FIELD_SPLIT_RE.split(value):
        field = raw.strip(" `*'\"：:()（）")
        field = re.sub(r"^(?:请|帮我|列出|输出|返回|展示|给出)\s*", "", field)
        if field and field not in fields:
            fields.append(field)
    return tuple(fields)


def _parse_count(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    return _ZH_NUMBERS.get(value)


def _parse_explicit_item_count(user_prompt: str) -> int | None:
    """Parse an exact requested result cardinality, never an approximate bound."""

    for pattern in (_ZH_EXACT_ITEM_COUNT_RE, _EN_EXACT_ITEM_COUNT_RE):
        for match in pattern.finditer(user_prompt):
            prefix = user_prompt[max(0, match.start() - 24) : match.start()]
            if _APPROXIMATE_COUNT_PREFIX_RE.search(prefix):
                continue
            raw_count = match.group("count").lower()
            if raw_count.isdigit():
                return int(raw_count)
            return _ZH_NUMBERS.get(raw_count) or _EN_NUMBERS.get(raw_count)
    return None


def _fold(value: str) -> str:
    return re.sub(r"[\s`*_：:()（）]", "", value).lower()
