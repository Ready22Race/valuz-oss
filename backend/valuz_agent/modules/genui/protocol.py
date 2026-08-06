"""A2UI prompt and payload assembly for the ``generate_ui`` tool.

A2UI v0.9 is the one wire protocol. The tool used to be able to emit OpenUI
Lang instead, chosen by ``VALUZ_GENUI_PROTOCOL``; carrying two generation
formats meant two prompt vocabularies, two renderers and two sets of failure
modes for one feature, so the second was removed rather than maintained.
"""

from __future__ import annotations

import json
from importlib import resources
from typing import Literal

OUTPUT_FORMAT = "A2UI v0.9 JSON message stream"

#: Which layer of the component vocabulary one generation is offered.
#:
#: The catalog is the bulk of every ``generate_ui`` prompt — ~64k characters
#: with everything against ~3k for the primitives alone — so letting the caller
#: pick a layer is the difference between paying for a hundred and fifty
#: components and paying for the ones the answer can actually use. A shorter
#: menu is also an easier menu: the model chooses better from it.
#:
#: Narrowing is prompt-side only. The renderer keeps accepting every component
#: it ever accepted, so a narrowed prompt can never produce a payload the client
#: cannot draw — the failure direction runs the other way, and that one stays
#: closed.
GenUIComponentScope = Literal["all", "edition", "atoms"]

_A2UI_INSTRUCTIONS_BASE = (
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
    "components one-for-one."
)

_A2UI_PREFER_BLOCKS = (
    " For financial market dashboards, prefer the Valuz "
    "semantic components in the catalog: they are rendered as OpenUI surfaces "
    "but avoid fragile Card/TextContent/Chart compositions."
)

_A2UI_NO_PLACEHOLDER_CHARTS = (
    " Do not create "
    "placeholder charts: only render chart components when the request or data "
    "contains real chart series, labels, slices, or points. When the data is a "
    "current snapshot rather than a time series, use {fallbacks} instead of an "
    "empty chart."
)

# What to fall back to when the data has no chart-ready series. Named per scope
# because a fallback the catalog does not offer is worse than no advice at all:
# the model is being told to reach for something it was never shown.
_A2UI_SNAPSHOT_FALLBACKS: dict[str, str] = {
    "all": "MarketIndexGrid, StatsCard, MarketBreadth, DataList, or Table",
    "edition": "MarketIndexGrid, StatsCard, MarketBreadth, or DataList",
    "atoms": "Table, TagBlock, or a plain TextContent row",
}

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
"""

_A2UI_MESSAGE_SHAPE = """\
Use official A2UI v0.9 component objects with component properties at the top
level, not nested under "props":
{"id":"title","component":"TextContent","text":"Revenue","size":"large-heavy"}
Use flat component ids for layout children:
{"id":"root","component":"Stack","children":["title","chart"],"direction":"column","gap":"m"}
Do not create placeholder charts or charts with empty series. If supplied data
does not include chart-ready arrays, show the raw values with {fallbacks}."""


def _load_block_catalog() -> str:
    """The Valuz block section of the catalog.

    Generated from the block registry in ``@valuz/genui-blocks`` by
    ``frontend/packages/ui/scripts/gen_openui_prompt.mjs`` — the same registry
    ``A2UIRenderer`` builds its component list from, so the model is never told
    about a block that cannot render, nor left unaware of one that can. Hand-
    editing this asset re-opens exactly that drift.
    """

    return (
        resources.files("valuz_agent.modules.genui")
        .joinpath("a2ui_block_catalog.txt")
        .read_text(encoding="utf-8")
        .rstrip("\n")
    )


_A2UI_ROOT_ONLY_CATALOG = """
OpenUI component catalog supported by the A2UI renderer:
- Layout: Stack — the document root, and the only OpenUI primitive offered
  here. Everything else comes from the Valuz blocks below.
"""

A2UI_COMPONENT_CATALOG = f"""{A2UI_OPENUI_COMPONENT_CATALOG}
- Valuz blocks (cards, citations, report pages, diagrams):
{_load_block_catalog()}
"""


def build_a2ui_catalog(scope: GenUIComponentScope = "all") -> str:
    """The A2UI catalog for one scope.

    Assembled rather than stored per scope because A2UI's primitive list is a
    hand-written blob (the renderer maps those names one-for-one) while the
    block half is generated — only the second half has a build step to hang a
    variant on.
    """

    fallbacks = _A2UI_SNAPSHOT_FALLBACKS[scope]
    if scope == "atoms":
        components = A2UI_OPENUI_COMPONENT_CATALOG
    elif scope == "edition":
        components = (
            f"{_A2UI_ROOT_ONLY_CATALOG}"
            "- Valuz blocks (cards, citations, report pages, diagrams):\n"
            f"{_load_block_catalog()}\n"
        )
    else:
        components = (
            f"{A2UI_OPENUI_COMPONENT_CATALOG}\n"
            "- Valuz blocks (cards, citations, report pages, diagrams):\n"
            f"{_load_block_catalog()}\n"
        )
    # `.replace`, not `.format`: the message-shape text is JSON, and every brace
    # in it would be read as a format field.
    return f"{components}{_A2UI_MESSAGE_SHAPE.replace('{fallbacks}', fallbacks)}\n"


def normalize_component_scope(value: object) -> GenUIComponentScope:
    """Read a caller-supplied scope, defaulting to the whole vocabulary.

    Tolerant on purpose: this argument is written by a model, and an unusable
    value should cost the wider prompt rather than the whole generation.
    """

    if isinstance(value, str):
        normalized = value.strip().lower().replace("-", "_")
        if normalized in {"all", "full", "everything"}:
            return "all"
        if normalized in {"edition", "blocks", "valuz", "semantic"}:
            return "edition"
        if normalized in {"atoms", "atom", "openui", "primitives", "basic"}:
            return "atoms"
    return "all"


def a2ui_instructions(scope: GenUIComponentScope = "all") -> str:
    """The A2UI system instructions, saying only what this scope can back up."""

    prefer_blocks = _A2UI_PREFER_BLOCKS if scope != "atoms" else ""
    tail = _A2UI_NO_PLACEHOLDER_CHARTS.replace(
        "{fallbacks}", _A2UI_SNAPSHOT_FALLBACKS[scope]
    )
    return f"{_A2UI_INSTRUCTIONS_BASE}{prefer_blocks}{tail}"


A2UI_GENERATIVE_UI_INSTRUCTIONS = a2ui_instructions()


def build_a2ui_prompt(
    request: str,
    data: object | None = None,
    scope: GenUIComponentScope = "all",
) -> str:
    parts = [
        a2ui_instructions(scope),
        "",
        "A2UI v0.9 message contract:",
        '- createSurface: {"version":"v0.9","createSurface":{"surfaceId":"main","catalogId":"openui"}}',
        '- updateDataModel: {"version":"v0.9","updateDataModel":{"surfaceId":"main","path":"/","value":{...}}}',
        '- updateComponents: {"version":"v0.9","updateComponents":{"surfaceId":"main","components":[...]}}',
        "- deleteSurface is allowed only when removing a surface.",
        '- every UI must include a component with id "root"; put the visible tree under root.children.',
        "",
        build_a2ui_catalog(scope).strip(),
        "",
        "REQUEST:",
        request.strip(),
    ]
    if data is not None:
        parts.append("")
        parts.append("DATA (render these values directly into the components):")
        parts.append(json.dumps(data, ensure_ascii=False))
    return "\n".join(parts)


def wrap_generated_ui(content: str) -> str:
    """Wrap the generated stream in the envelope the client dispatches on."""

    body = (content or "").strip()
    return json.dumps(
        {"protocol": "a2ui-json", "content": body},
        ensure_ascii=False,
        separators=(",", ":"),
    )
