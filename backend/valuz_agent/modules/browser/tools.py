"""``browser_start`` / ``browser_stop`` — host management tools (toolkit MCP).

Model-callable, but their IMPLEMENTATION is host code (``modules.browser.service``),
so daemon policy — profile path, ``--headless``, launch-vs-attach mode — stays
host-owned while the *trigger* is the model (lazy, in-session). Page operations
(navigate/click/snapshot/…) are NOT tools: the agent runs them via shell using
the ``cli_prefix`` ``browser_start`` returns. See
docs/design/browser-feature.md §1 (architecture).

Registered into the toolkit MCP ``base``/``lead`` toolsets at boot, so every
runtime (claude/codex/deepagents) sees them through its standard MCP client as
``mcp__harness__browser_start`` / ``…browser_stop``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.core import ToolDef, ToolResult
from src.core.tools import ExecContext

import valuz_agent.boot.kernel  # noqa: F401  (sets kernel import path)
from valuz_agent.modules.browser import service
from valuz_agent.modules.browser.errors import BrowserError

logger = logging.getLogger(__name__)

BROWSER_START_TOOL = "browser_start"
BROWSER_STOP_TOOL = "browser_stop"

_NO_ARGS: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}

_START_DESCRIPTION = (
    "Start (or reuse) the managed browser so you can drive it. Call this ONCE "
    "before running any chrome-devtools command. Returns JSON whose `cli_prefix` "
    "is the exact command prefix to use for every subsequent browser command. "
    "On error, relay the message to the user and stop."
)
_STOP_DESCRIPTION = (
    "Stop the managed browser when finished. Optional — the host also stops it on idle / app exit."
)


async def _start_handler(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
    try:
        result = await service.start()
    except BrowserError as exc:
        return ToolResult(content=exc.message, is_error=True)
    except Exception as exc:  # noqa: BLE001 — surface as a tool error, never crash the turn
        logger.exception("browser_start failed")
        return ToolResult(content=f"browser_start failed: {exc}", is_error=True)
    return ToolResult(content=json.dumps(result.model_dump(), ensure_ascii=False))


async def _stop_handler(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
    try:
        await service.stop()
    except Exception as exc:  # noqa: BLE001 — surface as a tool error, never crash the turn
        logger.exception("browser_stop failed")
        return ToolResult(content=f"browser_stop failed: {exc}", is_error=True)
    return ToolResult(content=json.dumps({"status": "stopped"}, ensure_ascii=False))


def build_browser_tool_defs() -> tuple[ToolDef, ...]:
    """Build the ``browser_start`` / ``browser_stop`` tool defs for the toolkit MCP."""
    return (
        ToolDef(
            name=BROWSER_START_TOOL,
            description=_START_DESCRIPTION,
            parameters=_NO_ARGS,
            handler=_start_handler,
        ),
        ToolDef(
            name=BROWSER_STOP_TOOL,
            description=_STOP_DESCRIPTION,
            parameters=_NO_ARGS,
            handler=_stop_handler,
        ),
    )
