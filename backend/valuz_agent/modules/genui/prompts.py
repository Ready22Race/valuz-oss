"""How ``generate_ui`` describes itself to the calling agent.

The prompt the *generator* reads lives in ``protocol.py`` beside the catalog it
is assembled from. This is the other audience: the tool description is what
decides whether the tool gets called at all, and by an agent that has never
seen the catalog.
"""

from __future__ import annotations

TOOL_DESCRIPTION = (
    "Generate a rich, interactive UI — charts, forms, KPI cards, or a dashboard — "
    "only when the user has asked for a chart, dashboard, visualization, or "
    "interactive UI — in this message or recently in this conversation, so a "
    "follow-up refining a chart already on screen still counts even when it "
    "does not name one. Never infer this intent from data, and do "
    "not call it merely because the user asks to list items or show a table. Pass "
    "a natural-language `request` describing what to show, and optional `data`. "
    "Optionally narrow `components` to 'edition' (curated semantic blocks — KPI "
    "cards, market tiles, report pages, citations) or 'atoms' (OpenUI "
    "primitives — layout, text, tables, charts, forms) when the shape of the "
    "answer is already clear; it generates faster from the smaller set. "
    "The client renders the returned GenUI protocol payload inline; do not repeat "
    "the same content as text afterwards."
)
