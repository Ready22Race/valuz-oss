"""project-config in-process tool: set/append the project's instructions.

Mirrors the memory tool (``modules/memory/tools.py``): a single tool registered
in the host toolkit MCP ``base`` toolset (runtime-agnostic — claude/codex/
deepagents), self-gating to PROJECT sessions via the calling session's
host-stamped ``metadata.valuz.project_id`` (the kernel knows no projects).

Why instructions are NOT delivered like memory
-----------------------------------------------
Project memory rides per-turn ``additional_context`` (rebuilt every turn), so it
takes effect immediately and stays prompt-cache-safe. Project *instructions* are
the project's authoritative direction/framework — they live in
``project.instructions_md`` and flow into a session's SYSTEM PROMPT
(``session.instructions``), which is frozen at session creation (ADR-008). So an
edit here applies to the project's NEXT conversation, not the current turn —
exactly like Claude Code's ``CLAUDE.md`` (read once per conversation, re-read on
the next one). The tool description tells the agent this so it doesn't promise an
immediate effect.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.core import ToolDef, ToolResult
from src.core.tools import ExecContext

import valuz_agent.boot.kernel  # noqa: F401  (sets kernel import path)
from valuz_agent.adapters import kernel_client
from valuz_agent.infra.auth_context import require_current_user_id

logger = logging.getLogger(__name__)

PROJECT_INSTRUCTIONS_TOOL_NAME = "project_instructions"
_ACTIONS = ("get", "set", "append")

TOOL_DESCRIPTION = (
    "Read or configure THIS project's instructions (its 项目说明 / direction, "
    "analysis framework, output preferences). Project sessions only — unavailable "
    "in a quick chat or agent-only conversation. Edits the project's persistent "
    "instructions that seed every conversation's system prompt.\n"
    "- action=get: return the project's CURRENT full instructions text. Use this "
    "first when editing, so you can modify the full text deliberately.\n"
    "- action=set: replace the WHOLE instructions text with `content` (the "
    "read-then-edit-the-whole-thing flow: get → revise → set).\n"
    "- action=append: add `content` as a new paragraph below the existing text "
    "(shortcut for purely additive notes).\n"
    "Edits take effect for the project's NEXT conversation (the current "
    "conversation keeps the system prompt it started with) — say so if the user "
    "expects an immediate change. For facts/progress that should apply right "
    "away, use the `memory` tool with target=project instead."
)


async def _resolve_project_id(user_id: str, session_id: str) -> str | None:
    """Project id for the calling session — read from the host-stamped
    ``metadata.valuz.project_id``. Returns None for quick chats / agent-only
    sessions (no project), which gates the tool to project sessions."""
    if not session_id:
        return None
    sess = await kernel_client.get_session(user_id, session_id)
    if sess is None:
        return None
    return ((sess.metadata or {}).get("valuz", {}) or {}).get("project_id") or None


async def _handler(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
    action = args.get("action")
    content = args.get("content")

    if action not in _ACTIONS:
        return ToolResult(
            content="project_instructions: 'action' must be get|set|append", is_error=True
        )
    if action in ("set", "append") and (not content or not str(content).strip()):
        return ToolResult(
            content="project_instructions: 'content' is required for set/append", is_error=True
        )

    # MCP tool boundary: the toolkit server published the caller's owner into
    # the auth context — resolve it once here and thread it explicitly.
    user_id = require_current_user_id()
    project_id = await _resolve_project_id(user_id, ctx.session_id)
    if not project_id:
        return ToolResult(
            content=(
                "project_instructions: this session has no project — project "
                "instructions can only be read/configured inside a project"
            ),
            is_error=True,
        )

    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.infra.eventbus import event_bus
    from valuz_agent.modules.projects.datastore import ProjectDatastore
    from valuz_agent.modules.projects.service import ProjectService

    try:
        # Read path: return the current full instructions (the read half of a
        # deliberate read → revise → set edit).
        if action == "get":
            async with async_unit_of_work(commit=False) as db:
                row = await ProjectDatastore(db).get_by_id(user_id, project_id)
            if row is None:
                return ToolResult(content="project_instructions: project not found", is_error=True)
            return ToolResult(
                content=json.dumps({"instructions": row.instructions_md or ""}, ensure_ascii=False)
            )

        text = str(content).strip()
        async with async_unit_of_work(commit=True) as db:
            ds = ProjectDatastore(db)
            svc = ProjectService(ds, event_bus)
            if action == "append":
                row = await ds.get_by_id(user_id, project_id)
                if row is None:
                    return ToolResult(
                        content="project_instructions: project not found", is_error=True
                    )
                base = (row.instructions_md or "").strip()
                text = f"{base}\n\n{text}".strip() if base else text
            await svc.update_instructions(user_id, project_id, text)
    except KeyError:
        return ToolResult(content="project_instructions: project not found", is_error=True)
    except Exception as exc:  # noqa: BLE001
        logger.exception("project_instructions tool failed")
        return ToolResult(content=f"project_instructions failed: {exc}", is_error=True)

    return ToolResult(
        content=json.dumps(
            {
                "success": True,
                "action": action,
                "applies_to": "the project's next conversation (current one is unchanged)",
            },
            ensure_ascii=False,
        )
    )


_PARAMS = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": list(_ACTIONS), "description": "get|set|append."},
        "content": {
            "type": "string",
            "description": (
                "Instructions text. Omit for get. For set: the full revised text. "
                "For append: the paragraph to add."
            ),
        },
    },
    "required": ["action"],
}


def build_project_instructions_tool_defs() -> tuple[ToolDef, ...]:
    """Build the single ``project_instructions`` tool def for the toolkit MCP."""
    td = ToolDef(
        name=PROJECT_INSTRUCTIONS_TOOL_NAME,
        description=TOOL_DESCRIPTION,
        parameters=_PARAMS,
        handler=_handler,
        read_only=False,
    )
    logger.info("Built project tool def: %s", PROJECT_INSTRUCTIONS_TOOL_NAME)
    return (td,)
