"""Edition-registered always-on internal MCP servers.

The four built-in always-on servers (docs / automations / connectors /
harness) are hardcoded in ``adapters/capability_resolver``. Editions need the
same channel for their own domain tools (e.g. finance thesis/binding tools)
without forking the resolver: they mount an internal ASGI MCP server via
``EditionApplication.register_api`` and append a spec here.

The resolver builds the full ``McpHttpServerConfig`` itself — URL from the
backend base + ``{path}/mcp``, plus the same internal credential headers and
tool timeout as the built-ins — so editions never handle the sandbox
credential. List semantics: editions append, they do not replace; reserved
built-in names are skipped defensively.
"""

from __future__ import annotations

from dataclasses import dataclass

RESERVED_ALWAYS_ON_NAMES = frozenset(
    {"valuz_docs", "valuz_automations", "valuz_connectors", "harness"}
)

__all__ = ["AlwaysOnMcpServerSpec", "RESERVED_ALWAYS_ON_NAMES"]


@dataclass(frozen=True)
class AlwaysOnMcpServerSpec:
    """One edition-owned always-on MCP server.

    ``name`` is the model-visible server name (tools appear as
    ``mcp__{name}__*``); ``path`` is the internal ASGI mount path WITHOUT the
    trailing ``/mcp`` (e.g. ``/_internal/mcp/finance/base``), mounted outside
    ``api_prefix`` via ``register_api``.
    """

    name: str
    path: str
