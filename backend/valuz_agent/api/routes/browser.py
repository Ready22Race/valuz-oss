"""HTTP layer for the Settings "Browser" panel.

Thin wrappers over ``modules.browser.service`` (the same service the
``browser_start``/``browser_stop`` MCP tools call). The panel is the human
front door — status / diagnostics / login helper — while the MCP tools are the
agent's lazy-activation front door. See
docs/design/browser-feature.md §1 (architecture).

``open`` raises ``BrowserError`` (e.g. Node missing) which the app middleware
maps to a 422 with the error's message; the panel surfaces it as a hint.
"""

from __future__ import annotations

from fastapi import APIRouter

from valuz_agent.modules.browser import service
from valuz_agent.modules.browser.schemas import (
    BrowserStartResult,
    BrowserStatus,
    BrowserStopResult,
)

router = APIRouter(prefix="/v1/browser", tags=["browser"])


@router.get("/status", response_model=BrowserStatus)
async def get_browser_status() -> BrowserStatus:
    """Cheap, read-only snapshot for the Settings panel (safe to poll)."""
    return await service.status()


@router.post("/open", response_model=BrowserStartResult)
async def open_browser() -> BrowserStartResult:
    """Login helper: start (or reuse) the managed browser so the user can log
    into sites in the isolated profile. Raises ``BrowserError`` (→ 422) when the
    environment isn't ready (e.g. Node missing)."""
    return await service.start()


@router.post("/stop", response_model=BrowserStopResult)
async def stop_browser() -> BrowserStopResult:
    await service.stop()
    return BrowserStopResult()
