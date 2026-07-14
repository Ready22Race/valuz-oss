"""genui ids — tool_use_id discovery by input fingerprint."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import valuz_agent.modules.genui.ids as ids


def test_normalize_input_sorts_keys_and_ignores_order():
    assert ids.normalize_input({"b": 1, "a": 2}) == ids.normalize_input({"a": 2, "b": 1})


def test_normalize_input_empty():
    assert ids.normalize_input(None) == ids.normalize_input({}) == "{}"


def _ev(type_: str, data: dict) -> SimpleNamespace:
    return SimpleNamespace(type=type_, data=data)


@pytest.fixture
def patched(monkeypatch):
    captured: dict = {}

    async def _window(user_id, session_id, *, turn_limit=20):
        captured["args"] = (user_id, session_id, turn_limit)
        return SimpleNamespace(items=captured.pop("events", []))

    monkeypatch.setattr(ids.kernel_client, "get_events_window", _window)
    return captured


async def test_resolves_by_matching_input(patched):
    patched["events"] = [
        _ev("tool_use", {"id": "OTHER", "name": "generate_ui", "input": {"request": "other"}}),
        _ev("tool_use", {"id": "R1", "name": "generate_ui", "input": {"request": "chart", "data": {"x": 1}}}),
    ]
    r = await ids.resolve_tool_use_id(
        user_id="u1", session_id="s1", arguments={"data": {"x": 1}, "request": "chart"}
    )
    assert r == "R1"  # input 指纹命中(顺序无关)


@pytest.mark.parametrize(
    "tool_name",
    [
        "generate_ui",  # deepagents — bare
        "mcp__harness__generate_ui",  # claude_agent
        "harness/generate_ui",  # codex
    ],
)
async def test_resolves_across_runtime_namespacing(patched, tool_name):
    """The tool_use name is MCP-namespaced per runtime; the matcher must accept
    all three forms (mirrors the frontend's isToolNamed)."""
    patched["events"] = [
        _ev("tool_use", {"id": "R-NS", "name": tool_name, "input": {"request": "chart"}}),
    ]
    r = await ids.resolve_tool_use_id(user_id="u1", session_id="s1", arguments={"request": "chart"})
    assert r == "R-NS"


async def test_recency_tiebreak_on_identical_input(patched):
    patched["events"] = [
        _ev("tool_use", {"id": "OLD", "name": "generate_ui", "input": {"request": "same"}}),
        _ev("tool_use", {"id": "NEW", "name": "generate_ui", "input": {"request": "same"}}),
    ]
    r = await ids.resolve_tool_use_id(user_id="u1", session_id="s1", arguments={"request": "same"})
    assert r == "NEW"  # 取最近一条


async def test_ignores_other_tools(patched):
    patched["events"] = [
        _ev("tool_use", {"id": "X", "name": "memory", "input": {"request": "chart"}}),
    ]
    assert await ids.resolve_tool_use_id(user_id="u1", session_id="s1", arguments={"request": "chart"}) is None


async def test_no_session_returns_none():
    assert await ids.resolve_tool_use_id(user_id="u1", session_id="", arguments={"request": "x"}) is None


async def test_get_events_window_failure_returns_none(patched, monkeypatch):
    async def _boom(*a, **k):
        raise RuntimeError("net")

    monkeypatch.setattr(ids.kernel_client, "get_events_window", _boom)
    assert await ids.resolve_tool_use_id(user_id="u1", session_id="s1", arguments={"request": "x"}) is None
