"""Codex event mapper: ``webSearch`` thread items must surface as tool events.

Regression for a silent drop: codex's built-in web search emits
``item/started`` / ``item/completed`` with a ``WebSearchThreadItem``
(app-server item type ``webSearch — {id, query, action?}``), but the
mapper only handled command / fileChange / mcpToolCall items, so a
web search never produced ``tool_use`` / ``tool_result`` events and
the client showed nothing for it.

Mapping semantics: the started snapshot is an empty placeholder
(``query: ""``, ``action: {type: "other"}``) and is ignored. Codex never
exposes the fetched results — the action is all there is — so the pair
splits it: tool_use input carries just the action *type*, tool_result
content carries the full action.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

import json

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from openai_codex.generated.v2_all import (
    ItemCompletedNotification,
    ItemStartedNotification,
    ThreadItem,
    WebSearchAction,
    WebSearchThreadItem,
)
from openai_codex.models import Notification
from src.runtimes.codex.event_mapper import map_notification


def _web_search_item(query: str, action: dict | None = None) -> ThreadItem:
    return ThreadItem(
        root=WebSearchThreadItem(
            id="ws_123",
            type="webSearch",
            query=query,
            action=WebSearchAction.model_validate(action) if action else None,
        )
    )


def _completed(item: ThreadItem) -> Notification:
    return Notification(
        method="item/completed",
        payload=ItemCompletedNotification.model_validate(
            {"item": item, "completedAtMs": 2, "threadId": "th_1", "turnId": "tu_1"}
        ),
    )


def test_web_search_item_started_placeholder_is_dropped() -> None:
    # Codex's started snapshot carries no real data — query is empty and
    # the action is the "other" placeholder. Emitting it would render a
    # junk `{"query": "", "action": {"type": "other"}}` input in the UI.
    notification = Notification(
        method="item/started",
        payload=ItemStartedNotification.model_validate(
            {
                "item": _web_search_item("", {"type": "other"}),
                "startedAtMs": 1,
                "threadId": "th_1",
                "turnId": "tu_1",
            }
        ),
    )

    assert map_notification(notification) == []


def test_web_search_search_action_type_in_input_full_action_in_result() -> None:
    action = {
        "type": "search",
        "query": "贵州茅台 2026 最新公告",
        "queries": ["贵州茅台 2026 最新公告", "贵州茅台 2026 半年度业绩"],
    }
    events = map_notification(_completed(_web_search_item("贵州茅台 2026 最新公告", action)))

    assert [e.type for e in events] == ["tool_use", "tool_result"]
    assert events[0].data == {
        "id": "ws_123",
        "name": "web_search",
        "input": {"action": {"type": "search"}},
    }
    assert events[1].data["id"] == "ws_123"
    assert events[1].data["is_error"] is False
    assert json.loads(events[1].data["content"]) == action


def test_web_search_open_page_action_type_in_input_url_in_result() -> None:
    events = map_notification(
        _completed(
            _web_search_item(
                "https://example.com/ir",
                {"type": "openPage", "url": "https://example.com/ir"},
            )
        )
    )

    assert events[0].data["input"] == {"action": {"type": "openPage"}}
    assert json.loads(events[1].data["content"]) == {
        "type": "openPage",
        "url": "https://example.com/ir",
    }


def test_web_search_without_action_falls_back_to_query() -> None:
    events = map_notification(_completed(_web_search_item("moutai investor relations")))

    assert events[0].data["input"] == {"query": "moutai investor relations"}
    assert json.loads(events[1].data["content"]) == {
        "id": "ws_123",
        "type": "webSearch",
        "query": "moutai investor relations",
    }
