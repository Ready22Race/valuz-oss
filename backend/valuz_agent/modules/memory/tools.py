"""memory in-process MCP tool (memory-system-design §5).

A single ``memory`` tool with actions add/replace/remove (no read — memory is
injected, so reading is implicit). Registered in the host toolkit MCP ``base``
toolset, so it is runtime-agnostic (claude/codex/deepagents). The handler
resolves the ``project`` target from the calling session's host-stamped
``metadata.valuz.project_id`` (the kernel knows no projects); ``user`` /
``global`` need no project. Both this tool and the background extractor (P1)
call the same ``MemoryStore`` pipeline — only the ``source`` tag differs.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.core import ToolDef, ToolResult
from src.core.tools import ExecContext

import valuz_agent.boot.kernel  # noqa: F401  (sets kernel import path)
from valuz_agent.adapters import kernel_client
from valuz_agent.modules.memory.models import TARGETS
from valuz_agent.modules.memory.prompts import TOOL_DESCRIPTION
from valuz_agent.modules.memory.service import MemoryError, memory_store

logger = logging.getLogger(__name__)

MEMORY_TOOL_NAME = "memory"
_ACTIONS = ("add", "replace", "remove")


# --- context resolution (module-level so tests can monkeypatch) -------------


async def _resolve_project_id(user_id: str, session_id: str) -> str | None:
    """Project id for the session — read from the host-stamped
    ``metadata.valuz.project_id`` (the kernel knows no projects). Returns None
    for quick chats / agent-only sessions (only user+global are writable there)."""
    if not session_id:
        return None
    sess = await kernel_client.get_session(user_id, session_id)
    if sess is None:
        return None
    return ((sess.metadata or {}).get("valuz", {}) or {}).get("project_id") or None


# --- handler ----------------------------------------------------------------


async def _memory_handler(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
    user_id = ctx.user_id

    action = args.get("action")
    target = args.get("target")
    content = args.get("content")
    old_text = args.get("old_text")

    if action not in _ACTIONS:
        return ToolResult(content="memory: 'action' must be add|replace|remove", is_error=True)
    if target not in TARGETS:
        return ToolResult(content="memory: 'target' must be user|global|project", is_error=True)
    if action == "add" and not content:
        return ToolResult(content="memory: 'content' is required for add", is_error=True)
    if action == "replace" and (not old_text or not content):
        return ToolResult(
            content="memory: 'old_text' and 'content' are required for replace", is_error=True
        )
    if action == "remove" and not old_text:
        return ToolResult(content="memory: 'old_text' is required for remove", is_error=True)

    project_id: str | None = None
    if target == "project":
        # MCP tool boundary: the toolkit server has published the caller's owner
        # into the auth context — resolve it once here and thread it explicitly.
        project_id = await _resolve_project_id(user_id, ctx.session_id)
        if not project_id:
            return ToolResult(
                content=(
                    "memory: 'project' target unavailable here (this session has no "
                    "project) — use 'user' or 'global'"
                ),
                is_error=True,
            )

    try:
        if action == "add":
            result = memory_store.add(target, str(content), project_id=project_id, source="agent")
        elif action == "replace":
            result = memory_store.replace(
                target, str(old_text), str(content), project_id=project_id, source="agent"
            )
        else:  # remove
            result = memory_store.remove(
                target, str(old_text), project_id=project_id, source="agent"
            )
    except MemoryError as exc:
        return ToolResult(content=f"memory: {exc}", is_error=True)
    except Exception as exc:  # noqa: BLE001
        logger.exception("memory tool failed")
        return ToolResult(content=f"memory failed: {exc}", is_error=True)

    return ToolResult(
        content=json.dumps(result, ensure_ascii=False),
        is_error=not bool(result.get("success", True)),
    )


# --- schema -----------------------------------------------------------------

_TARGET_PROP = {
    "type": "string",
    "enum": list(TARGETS),
    "description": (
        "user=who the user is (cross-project); global=cross-project notes/lessons; "
        "project=this project (project sessions only)."
    ),
}
_PARAMS = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": list(_ACTIONS), "description": "add|replace|remove."},
        "target": _TARGET_PROP,
        "content": {"type": "string", "description": "Entry text. Required for add and replace."},
        "old_text": {
            "type": "string",
            "description": "Unique substring identifying the entry to replace/remove.",
        },
    },
    "required": ["action", "target"],
}


def build_memory_tool_defs() -> tuple[ToolDef, ...]:
    """Build the single ``memory`` tool def (live handler) for the host toolkit MCP server."""
    td = ToolDef(
        name=MEMORY_TOOL_NAME,
        description=TOOL_DESCRIPTION,
        parameters=_PARAMS,
        handler=_memory_handler,
        read_only=False,
    )
    logger.info("Built memory tool def: %s", MEMORY_TOOL_NAME)
    return (td,)
