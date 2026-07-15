"""Resolve the calling tool_use id for a generate_ui invocation.

The host toolkit MCP server's handler gets ``(tool_name, arguments)`` but NOT
the runtime's tool_use id — the MCP ``@server.call_tool()`` decorator drops
``_meta``/``progressToken``. To key streamed ``tool_output_delta`` events to the
right frontend card we recover the id by matching the tool INPUT: the runtime
persists a ``tool_use`` event (carrying ``input``) on the calling session
before invoking the tool, and the handler received the same arguments. Distinct
concurrent calls have distinct inputs -> deterministic match; identical inputs
tiebreak by recency (identical output either way).
"""

from __future__ import annotations

import json
import logging
from typing import Any

import valuz_agent.boot.kernel  # noqa: F401  (sets kernel import path)
from valuz_agent.adapters import kernel_client

logger = logging.getLogger(__name__)


def normalize_input(value: Any) -> str:
    """Canonical JSON for an MCP arguments blob (sorted keys). Lets us compare
    the handler's ``arguments`` against a tool_use event's ``input`` without
    tripping on key order or None."""
    return json.dumps(value or {}, sort_keys=True, ensure_ascii=False, default=str)


def _is_generate_ui_call(name: Any) -> bool:
    """The generate_ui tool surfaces under runtime-specific MCP namespacing:
    claude_agent ``mcp__harness__generate_ui``, codex ``harness/generate_ui``,
    deepagents bare ``generate_ui``. Match all three — mirrors the frontend's
    ``isToolNamed`` so the backend resolves the id the frontend will key on."""
    if not isinstance(name, str) or not name:
        return False
    return (
        name == "generate_ui"
        or name.endswith("__generate_ui")
        or name.endswith("/generate_ui")
    )


async def resolve_tool_use_id(
    *, user_id: str, session_id: str, arguments: dict[str, Any]
) -> str | None:
    """The tool_use id of the generate_ui call that produced ``arguments`` on
    ``session_id``, or None if it can't be determined (caller then skips
    streaming and renders synchronously). Reads the recent calling-session
    event window, filters generate_ui tool_use blocks, matches by normalized
    input, tiebreaks by recency (last match wins). Best-effort: any failure ->
    None."""
    if not session_id:
        return None
    try:
        window = await kernel_client.get_events_window(user_id, session_id, turn_limit=20)
    except Exception:  # noqa: BLE001
        logger.debug("generate_ui: resolve_tool_use_id get_events_window failed", exc_info=True)
        return None

    target = normalize_input(arguments)
    match: str | None = None
    candidates = 0
    for ev in getattr(window, "items", None) or []:
        if getattr(ev, "type", None) != "tool_use":
            continue
        data = getattr(ev, "data", None) or {}
        if not _is_generate_ui_call(data.get("name")):
            continue
        candidates += 1
        if normalize_input(data.get("input")) != target:
            continue
        eid = data.get("id")
        if eid:
            match = str(eid)  # keep last (most recent) match
    logger.info(
        "generate_ui: resolve_tool_use_id session=%s generate_ui_candidates=%d -> tool_use_id=%s",
        session_id,
        candidates,
        match,
    )
    return match
