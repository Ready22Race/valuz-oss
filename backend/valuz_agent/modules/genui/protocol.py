"""GenUI protocol selection and prompt/payload helpers."""

from __future__ import annotations

import json
from typing import Literal

from valuz_agent.modules.genui.prompts import (
    GENERATIVE_UI_INSTRUCTIONS,
    build_openui_prompt,
)

GenUIProtocol = Literal["a2ui", "openui"]

A2UI_GENERATIVE_UI_INSTRUCTIONS = (
    "You generate user interfaces as an A2UI v0.9 JSON message stream. Output "
    "ONLY newline-delimited JSON objects, with no markdown fences, prose, or "
    "explanations. The first message must create a surface, and later messages "
    "may update its data model and components. Use concise component trees "
    "that fit inside an existing conversation pane; never generate an app shell, "
    "sidebar, top navigation, or fixed-width page chrome. Prefer compact, "
    "mobile-first layouts: KPI/detail rows may wrap, charts should occupy a "
    "readable full-width section, and tables may scroll horizontally only when "
    "their columns cannot stay readable. Use OpenUI component names from the "
    "catalog below so the @a2ui/react renderer can map them to OpenUI React "
    "components one-for-one. For financial market dashboards, prefer the Valuz "
    "semantic components in the catalog: they are rendered as OpenUI surfaces "
    "but avoid fragile Card/TextContent/Chart compositions. Do not create "
    "placeholder charts: only render chart components when the request or data "
    "contains real chart series, labels, slices, or points. When the data is a "
    "current snapshot rather than a time series, use MarketIndexGrid, "
    "FinanceMetric, MarketBreadth, DataList, or Table instead of an empty chart."
)

A2UI_OPENUI_COMPONENT_CATALOG = """
OpenUI component catalog supported by the A2UI renderer:
- Layout: Stack, Row, Grid, Tabs, TabItem, Accordion, AccordionItem, Steps,
  StepsItem, Carousel, Separator, Modal.
- Content: Card, Section, CardHeader, Heading, Title, TextContent, Text,
  Paragraph, MarkDownRenderer, Markdown, Callout, TextCallout, CodeBlock,
  Image, ImageBlock, ImageGallery.
- Tables: Table, Col.
- Charts: BarChart, LineChart, AreaChart, RadarChart, HorizontalBarChart,
  PieChart, RadialChart, SingleStackedBarChart, ScatterChart, Series,
  ScatterSeries, Point, Slice.
- Forms: Form, FormControl, Label, Input, TextArea, Select, SelectItem,
  DatePicker, Slider, CheckBoxGroup, CheckBoxItem, RadioGroup, RadioItem,
  SwitchGroup, SwitchItem.
- Actions/display: Button, Buttons, TagBlock, Tag, Metric, KPI, ListBlock,
  ListItem, List.
- Valuz semantic components:
  - MarketIndexGrid: props title, description, indices. Use for groups of
    market/index quote cards. Each index item may include name, code, latest,
    change, changePct, turnover, source, asOf.
  - MarketIndexCard: props name, code, latest, change, changePct, turnover,
    source, asOf. Use only for one standalone quote.
  - FinanceMetric: props label, value, unit, change, changePct, description.
    Use for PE/PB/market cap/revenue/margin/ROE/turnover-rate/valuation metrics.
  - MarketBreadth: props title, up, down, flat, total, source. Use for
    up/down/flat breadth summaries.
  - DataList: props title, description, items. Use for rankings, ordered
    records, sector movers, stock movers, holdings, news, risk flags, and other
    compact repeated textual data. Each item may include rank, name/title,
    description, value, changePct/meta, trend. Prefer DataList over free-form
    Row/Grid/TextContent lists; the renderer aligns rows as rank / main / value / meta
    and wraps responsively.
Use official A2UI v0.9 component objects with component properties at the top
level, not nested under "props":
{"id":"title","component":"TextContent","text":"Revenue","size":"large-heavy"}
Use flat component ids for layout children:
{"id":"root","component":"Stack","children":["title","chart"],"direction":"column","gap":"m"}
Do not create placeholder charts or charts with empty series. If supplied data
does not include chart-ready arrays, show the raw values with DataList, Table,
MarketIndexGrid, FinanceMetric, or MarketBreadth.
"""


def normalize_genui_protocol(value: object) -> GenUIProtocol:
    if isinstance(value, str):
        normalized = value.strip().lower().replace("_", "-")
        if normalized in {"a2ui", "a2ui-json", "a2ui-v0.9", "a2ui-0.9"}:
            return "a2ui"
        if normalized in {"openui", "openui-lang"}:
            return "openui"
    raise ValueError("genui protocol must be 'a2ui' or 'openui'")


def session_instructions_for_protocol(protocol: GenUIProtocol) -> str:
    if protocol == "a2ui":
        return A2UI_GENERATIVE_UI_INSTRUCTIONS
    return GENERATIVE_UI_INSTRUCTIONS


def output_format_for_protocol(protocol: GenUIProtocol) -> str:
    if protocol == "a2ui":
        return "A2UI v0.9 JSON message stream"
    return "OpenUI Lang"


def build_prompt_for_protocol(
    protocol: GenUIProtocol,
    request: str,
    data: object | None = None,
) -> str:
    if protocol == "openui":
        return build_openui_prompt(request, data)
    return build_a2ui_prompt(request, data)


def build_a2ui_prompt(request: str, data: object | None = None) -> str:
    parts = [
        A2UI_GENERATIVE_UI_INSTRUCTIONS,
        "",
        "A2UI v0.9 message contract:",
        '- createSurface: {"version":"v0.9","createSurface":{"surfaceId":"main","catalogId":"openui"}}',
        '- updateDataModel: {"version":"v0.9","updateDataModel":{"surfaceId":"main","path":"/","value":{...}}}',
        '- updateComponents: {"version":"v0.9","updateComponents":{"surfaceId":"main","components":[...]}}',
        "- deleteSurface is allowed only when removing a surface.",
        '- every UI must include a component with id "root"; put the visible tree under root.children.',
        "",
        A2UI_OPENUI_COMPONENT_CATALOG.strip(),
        "",
        "REQUEST:",
        request.strip(),
    ]
    if data is not None:
        parts.append("")
        parts.append("DATA (render these values directly into the components):")
        parts.append(json.dumps(data, ensure_ascii=False))
    return "\n".join(parts)


def wrap_generated_ui(protocol: GenUIProtocol, content: str) -> str:
    body = (content or "").strip()
    if protocol == "openui":
        return body
    return json.dumps(
        {"protocol": "a2ui-json", "content": body},
        ensure_ascii=False,
        separators=(",", ":"),
    )
