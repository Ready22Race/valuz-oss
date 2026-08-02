"""Deterministic output-scope contract parsed from the user request."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_STRICT_RE = re.compile(
    r"(?:只列出|只输出|只返回|只展示|只给出|仅列出|仅输出|仅返回|"
    r"只用\s*[一二两三四五六七八九十\d]+\s*行|"
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
    r"(?:数字|字段|指标|项目|项)(?:[。；\n]|$)",
    re.IGNORECASE,
)
_EN_FIELDS_RE = re.compile(
    r"\bonly\s+(?:list|output|return|show)\s+(?P<fields>[^.\n]{2,180}?)"
    r"(?:\s+and\s+nothing\s+else)?(?:[.\n]|$)",
    re.IGNORECASE,
)
_FIELD_SPLIT_RE = re.compile(r"\s*(?:、|，|,|/|以及|及|与|和|\band\b)\s*", re.IGNORECASE)
_TABLE_ONLY_RE = re.compile(
    r"(?:只|仅).{0,16}(?:输出|返回|列出).{0,16}(?:Markdown\s*)?表格|"
    r"\bonly\s+(?:output|return)\b.{0,24}\bmarkdown\s+table\b",
    re.IGNORECASE,
)
_GENERATED_UI_RE = re.compile(
    r"(?:图表|仪表盘|可视化|交互(?:页面|界面)|dashboard|chart|visuali[sz]ation|"
    r"interactive\s+(?:ui|interface))",
    re.IGNORECASE,
)
_ZH_PERIOD_COUNT_RE = re.compile(
    r"(?:最近|近|过去|此前|前)\s*(?P<count>[一二两三四五六七八九十\d]+)\s*个?"
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
    requested_line_count: int | None = None
    requested_period_count: int | None = None
    table_only: bool = False
    generated_ui_allowed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "strict": self.strict,
            "requestedFields": list(self.requested_fields),
            "requestedItemCount": self.requested_item_count,
            "requestedLineCount": self.requested_line_count,
            "requestedPeriodCount": self.requested_period_count,
            "tableOnly": self.table_only,
            "generatedUiAllowed": self.generated_ui_allowed,
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
    return OutputContract(
        strict=bool(_STRICT_RE.search(user_prompt)),
        requested_fields=requested_fields,
        requested_item_count=requested_count,
        requested_line_count=(
            _parse_count(line_match.group("count"))
            if (line_match := _LINE_COUNT_RE.search(user_prompt)) is not None
            else None
        ),
        requested_period_count=requested_period_count,
        table_only=bool(_TABLE_ONLY_RE.search(user_prompt)),
        generated_ui_allowed=bool(_GENERATED_UI_RE.search(user_prompt)),
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


def _fold(value: str) -> str:
    return re.sub(r"[\s`*_：:()（）]", "", value).lower()
