"""A declared MCP tool timeout must reach the Claude CLI.

The CLI aborts a silent MCP tool call after its own 300s default. Tools that
legitimately run longer with nothing to report in between (``generate_ui``
streams a whole document out of a model) declare ``tool_timeout_sec`` on their
server config; codex already maps it, and this pins the Claude path so the
same declaration is not silently dropped.
"""

from __future__ import annotations

from src.core.types import McpHttpServerConfig, McpStdioServerConfig
from src.runtimes.claude_agent.runtime import _to_sdk_mcp_server


def test_http_server_carries_the_declared_timeout_in_ms() -> None:
    entry = _to_sdk_mcp_server(
        McpHttpServerConfig(
            name="harness",
            url="http://127.0.0.1:8000/_internal/mcp/toolkit/base/mcp",
            tool_timeout_sec=720.0,
        )
    )
    assert entry["type"] == "http"
    assert entry["timeout"] == 720_000


def test_server_without_a_declared_timeout_stays_on_the_cli_default() -> None:
    entry = _to_sdk_mcp_server(
        McpHttpServerConfig(name="external", url="https://example.test/mcp")
    )
    assert "timeout" not in entry


def test_stdio_server_has_no_timeout_to_declare() -> None:
    """Only the HTTP/SSE shape carries ``tool_timeout_sec`` today; the stdio
    branch must not invent one (nor raise reading a field it lacks)."""
    entry = _to_sdk_mcp_server(
        McpStdioServerConfig(name="local", command="node", args=("server.js",))
    )
    assert entry["type"] == "stdio"
    assert "timeout" not in entry
