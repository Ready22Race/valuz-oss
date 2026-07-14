"""generative-UI in-process MCP tool — the ``generate_ui`` tool.

Registered in the host toolkit MCP ``base`` toolset (runtime-agnostic). The
handler resolves the caller's runtime/provider/model from the calling session,
builds the OpenUI prompt (vendored genui-lib + request + optional data), runs
one ephemeral no-tools LLM call via the memory-pattern completer, and returns
the OpenUI Lang as the tool result — which the frontend renders with OpenUI's
``<Renderer>``. Best-effort: every failure becomes an ``is_error`` result.
"""

from __future__ import annotations

import logging
from typing import Any

from src.core import ToolDef, ToolResult
from src.core.tools import ExecContext

import valuz_agent.boot.kernel  # noqa: F401  (sets kernel import path)
from valuz_agent.adapters import kernel_client
from valuz_agent.modules.genui.ids import resolve_tool_use_id
from valuz_agent.modules.genui.prompts import TOOL_DESCRIPTION, build_openui_prompt
from valuz_agent.modules.genui.runner import _make_completer, _resolve_provider_id
from valuz_agent.modules.providers.service import (
    resolve_model_provider_for_user as resolve_model_provider,
)

logger = logging.getLogger(__name__)

GENERATIVE_UI_TOOL_NAME = "generate_ui"

_PARAMS = {
    "type": "object",
    "properties": {
        "request": {
            "type": "string",
            "description": (
                "Natural-language description of the UI to generate — intent, "
                "layout, and what to show."
            ),
        },
        "data": {
            "type": "object",
            "description": "Optional structured values to render directly into the components.",
            "additionalProperties": True,
        },
    },
    "required": ["request"],
}


async def _generate_ui_handler(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
    user_id = ctx.user_id
    request = args.get("request")
    data = args.get("data")
    if not request or not str(request).strip():
        return ToolResult(content="generate_ui: 'request' is required", is_error=True)

    source = (
        await kernel_client.get_session(user_id, ctx.session_id) if ctx.session_id else None
    )
    if source is None:
        return ToolResult(
            content="generate_ui: no active session to resolve a model from",
            is_error=True,
        )

    provider_id = _resolve_provider_id(source)
    model = source.model
    runtime_provider = source.runtime_provider
    if not provider_id or not model:
        return ToolResult(
            content="generate_ui: could not resolve a model channel for this session",
            is_error=True,
        )

    try:
        mp = await resolve_model_provider(
            user_id=user_id,
            provider_id=str(provider_id),
            model_id=model,
            runtime_provider=runtime_provider,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("generate_ui: provider resolve failed", exc_info=True)
        return ToolResult(
            content=f"generate_ui: model channel unavailable ({exc})", is_error=True
        )

    tool_use_id = await resolve_tool_use_id(
        user_id=user_id, session_id=ctx.session_id, arguments=args
    )
    completer = _make_completer(
        user_id=user_id,
        runtime_provider=runtime_provider,
        model=model,
        mp=mp,
        calling_session_id=ctx.session_id if tool_use_id else None,
        tool_use_id=tool_use_id,
    )
    try:
        openui = await completer(build_openui_prompt(str(request), data))
    except Exception as exc:  # noqa: BLE001
        logger.debug("generate_ui: generation failed", exc_info=True)
        return ToolResult(content=f"generate_ui: generation failed ({exc})", is_error=True)

    openui = (openui or "").strip()
    if not openui:
        return ToolResult(
            content="generate_ui: model returned no OpenUI Lang", is_error=True
        )
    return ToolResult(content=openui, is_error=False)


def build_generative_ui_tool_defs() -> tuple[ToolDef, ...]:
    """Build the ``generate_ui`` tool def (live handler) for the host toolkit MCP server."""
    td = ToolDef(
        name=GENERATIVE_UI_TOOL_NAME,
        description=TOOL_DESCRIPTION,
        parameters=_PARAMS,
        handler=_generate_ui_handler,
        read_only=False,
    )
    logger.info("Built generative-ui tool def: %s", GENERATIVE_UI_TOOL_NAME)
    return (td,)
