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

#: Which set of components one generation is offered.
#:
#: The split follows where a component comes from, not what it is made of:
#:
#: - ``atoms`` — everything this repository ships: OpenUI's primitives *and*
#:   the built-in blocks. The general vocabulary.
#: - ``edition`` — only what an edition registered from outside this repo.
#:   A vertical's own set, unmixed with the general one.
#: - ``all`` — both. The default, and right when the shape of the answer is not
#:   known up front.
#:
#: A shorter menu is an easier menu: the model chooses better from one, and the
#: catalog is the bulk of every ``generate_ui`` prompt.
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
#
# The `edition` entry names nothing, and cannot: this repository does not know
# what an edition installed. Generic advice is the honest limit — better than
# naming components from a set that scope just excluded.
_A2UI_SNAPSHOT_FALLBACKS: dict[str, str] = {
    "all": "MarketIndexGrid, StatsCard, MarketBreadth, DataList, or Table",
    "atoms": "MarketIndexGrid, StatsCard, MarketBreadth, DataList, or Table",
    "edition": "a tile, list, or table component from the catalog above",
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
- Actions/display: Button, Buttons, TagBlock, Tag, Metric, KPI. For a list of
  entries use the DataList, StatusList, Timeline or Feed blocks below.
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


_A2UI_EDITION_HEADING = "- Edition components:\n"

_A2UI_ROOT_ONLY_CATALOG = """
OpenUI component catalog supported by the A2UI renderer:
- Layout: Stack — the document root, and the only component from the general
  vocabulary offered here. Everything else comes from the edition below.
"""

A2UI_COMPONENT_CATALOG = f"""{A2UI_OPENUI_COMPONENT_CATALOG}
- Valuz blocks (cards, citations, report pages, diagrams):
{_load_block_catalog()}
"""


def edition_catalog_text() -> str:
    """Components registered from outside this repository.

    Empty here, and that is the point: an edition is a separate build that
    vendors this one, so nothing in OSS registers into it. The backend registry
    that lets one is still to be built — see
    ``docs/design/genui-dynamic-blocks.md``. Until then this is the seam the
    scope reads, so the two land together rather than the scope being retrofitted.
    """

    return ""


def resolve_component_scope(scope: GenUIComponentScope) -> GenUIComponentScope:
    """The scope actually available, which is not always the one asked for.

    An ``edition`` scope with no edition registered would offer the root and
    nothing else — that does not produce a smaller answer, it produces no
    answer. Widening is the only safe direction when a scope turns out empty;
    narrowing to nothing is the failure this whole seam guards against.

    Resolved in one place so the catalog and the instructions cannot disagree
    about which scope is live — instructions naming a fallback the catalog never
    showed is the exact drift the scope exists to prevent.
    """

    if scope == "edition" and not edition_catalog_text():
        return "all"
    return scope


def build_a2ui_catalog(scope: GenUIComponentScope = "all") -> str:
    """The A2UI catalog for one scope.

    Assembled rather than stored per scope because A2UI's primitive list is a
    hand-written blob (the renderer maps those names one-for-one) while the
    block half is generated — only the second half has a build step to hang a
    variant on.
    """

    edition = edition_catalog_text()
    scope = resolve_component_scope(scope)

    own = (
        f"{A2UI_OPENUI_COMPONENT_CATALOG}\n"
        "- Valuz blocks (cards, citations, report pages, diagrams):\n"
        f"{_load_block_catalog()}\n"
    )
    installed = f"{_A2UI_EDITION_HEADING}{edition}\n" if edition else ""
    if scope == "atoms":
        components = own
    elif scope == "edition":
        # The root comes from the general set even here: it is the one component
        # an edition cannot supply for itself, since every document is rooted in
        # it before any edition component appears.
        components = f"{_A2UI_ROOT_ONLY_CATALOG}{installed}"
    else:
        # `all` is the union, in this order — an edition's components read as an
        # addition to the general vocabulary, which is what they are.
        components = f"{own}{installed}"

    fallbacks = _A2UI_SNAPSHOT_FALLBACKS[scope]
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
        if normalized in {"edition", "vertical"}:
            return "edition"
        if normalized in {"atoms", "atom", "blocks", "openui", "valuz", "base"}:
            return "atoms"
    return "all"


def a2ui_instructions(scope: GenUIComponentScope = "all") -> str:
    """The A2UI system instructions, saying only what this scope can back up."""

    scope = resolve_component_scope(scope)
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
