"""``browser_start`` / ``browser_stop`` toolkit tool defs.

Thin handlers over ``modules.browser.service`` — assert the tool surface and the
result/error mapping (service is patched; no real browser).
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede `from src.*`
from __future__ import annotations

import json

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from valuz_agent.integrations.toolkit_mcp_server import HostExecContext

from valuz_agent.modules.browser import service, tools
from valuz_agent.modules.browser.errors import BrowserNodeMissing
from valuz_agent.modules.browser.schemas import BrowserStartResult


def _tool(name: str):
    (td,) = [t for t in tools.build_browser_tool_defs() if t.name == name]
    return td


def test_tool_surface() -> None:
    defs = tools.build_browser_tool_defs()
    assert {d.name for d in defs} == {"browser_start", "browser_stop"}
    for d in defs:
        assert d.handler is not None
        assert d.parameters.get("properties") == {}  # no model-supplied args


async def test_start_handler_success(monkeypatch) -> None:
    async def fake_start(user_id: str) -> BrowserStartResult:
        assert user_id == "user-A"
        return BrowserStartResult(
            status="started", mode="managed", cli_prefix="npx -y -p x chrome-devtools"
        )

    monkeypatch.setattr(service, "start", fake_start)
    res = await _tool("browser_start").handler(
        {}, HostExecContext(session_id="s1", user_id="user-A")
    )
    assert res.is_error is False
    payload = json.loads(res.content)
    assert payload["status"] == "started"
    assert payload["cli_prefix"].endswith("chrome-devtools")


async def test_start_handler_node_missing(monkeypatch) -> None:
    async def fake_start(user_id: str) -> BrowserStartResult:
        assert user_id == "user-A"
        raise BrowserNodeMissing()

    monkeypatch.setattr(service, "start", fake_start)
    res = await _tool("browser_start").handler(
        {}, HostExecContext(session_id="s1", user_id="user-A")
    )
    assert res.is_error is True
    assert "Node" in res.content  # the install hint is surfaced


async def test_start_handler_requires_user_context(monkeypatch) -> None:
    res = await _tool("browser_start").handler({}, HostExecContext(session_id="s1"))
    assert res.is_error is True
    assert "user-scoped" in res.content


async def test_stop_handler(monkeypatch) -> None:
    called = {"v": False}

    async def fake_stop() -> None:
        called["v"] = True

    monkeypatch.setattr(service, "stop", fake_stop)
    res = await _tool("browser_stop").handler(
        {}, HostExecContext(session_id="s1", user_id="user-A")
    )
    assert res.is_error is False
    assert json.loads(res.content)["status"] == "stopped"
    assert called["v"] is True
