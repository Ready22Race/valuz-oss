"""Settings "Browser" panel routes — thin wrappers over ``modules.browser.service``.

Call the route handlers directly with the service patched (no real browser); the
``BrowserError`` → 422 mapping is the app middleware's job and tested there.
"""

from __future__ import annotations

import pytest

from valuz_agent.api.routes import browser as routes
from valuz_agent.modules.browser import service
from valuz_agent.modules.browser.errors import BrowserNodeMissing
from valuz_agent.modules.browser.schemas import (
    BrowserStartResult,
    BrowserStatus,
    BrowserStopResult,
)


async def test_status_route(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_status() -> BrowserStatus:
        return BrowserStatus(
            daemon_running=True, mode="managed", node_ok=True, cli_prefix="x", pid=7, hints=[]
        )

    monkeypatch.setattr(service, "status", fake_status)
    res = await routes.get_browser_status()
    assert isinstance(res, BrowserStatus)
    assert res.daemon_running is True
    assert res.pid == 7


async def test_open_route_success(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_start(_user_id: str) -> BrowserStartResult:
        return BrowserStartResult(status="started", mode="managed", cli_prefix="x")

    monkeypatch.setattr(service, "start", fake_start)
    res = await routes.open_browser()
    assert res.status == "started"


async def test_open_route_propagates_browser_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_start(_user_id: str) -> BrowserStartResult:
        raise BrowserNodeMissing()

    monkeypatch.setattr(service, "start", fake_start)
    with pytest.raises(BrowserNodeMissing):  # middleware maps this to 422
        await routes.open_browser()


async def test_stop_route(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"v": False}

    async def fake_stop() -> None:
        called["v"] = True

    monkeypatch.setattr(service, "stop", fake_stop)
    res = await routes.stop_browser()
    assert isinstance(res, BrowserStopResult)
    assert res.status == "stopped"
    assert called["v"] is True
