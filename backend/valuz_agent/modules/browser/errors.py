"""Browser module errors (subclass the shared ``infra/errors`` bases).

Module sequence = ``90`` (HTTP ``422`` + module ``90`` + seq). These surface
both through the ``browser_start`` MCP tool (folded into a ToolResult with a
hint) and the Settings HTTP routes (M1, mapped to 422 + message).
"""

from __future__ import annotations

from valuz_agent.infra.errors import UnprocessableEntityError


class BrowserError(UnprocessableEntityError):
    error_code = 422_900
    message = "Browser error"


class BrowserNodeMissing(BrowserError):
    error_code = 422_901
    message = (
        "Node.js (>= 20) was not found on PATH. The browser feature runs "
        "chrome-devtools via Node — install it from https://nodejs.org and retry."
    )


class BrowserStartFailed(BrowserError):
    error_code = 422_902
    message = "Failed to start the managed browser."
