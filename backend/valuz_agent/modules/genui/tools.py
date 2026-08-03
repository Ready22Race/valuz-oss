"""generative-UI in-process MCP tool — the ``generate_ui`` tool.

Registered in the host toolkit MCP ``base`` toolset (runtime-agnostic). The
handler resolves the caller's runtime/provider/model from the calling session,
builds the OpenUI prompt (vendored genui-lib + request + optional data), and
returns the OpenUI Lang as the tool result — which the frontend renders with
OpenUI's ``<Renderer>``. Official Claude/Codex subscription channels still run
through an ephemeral no-tools kernel session so their CLI keychain auth works;
explicit-credential channels call the model directly and stream chunks back to
the originating tool card. Best-effort: every failure becomes an ``is_error``
result.
"""

from __future__ import annotations

import asyncio
import logging
import re
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
_GENERATION_MAX_ATTEMPTS = 2
_GENERATION_RETRY_DELAY_SECONDS = 0.5
_EXPLICIT_VISUAL_REQUEST_RE = re.compile(
    r"(?:可视化|图表|画图|绘图|仪表盘|数据看板|交互(?:式)?(?:界面|图)|"
    r"\b(?:dashboard|chart|plot|graph|visuali[sz](?:e|ation)|interactive\s+ui)\b)",
    re.IGNORECASE,
)

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


async def _complete_with_retries(
    completer: Any,
    prompt: str,
    *,
    max_attempts: int = _GENERATION_MAX_ATTEMPTS,
) -> str:
    max_attempts = max(1, max_attempts)
    for attempt in range(1, max_attempts + 1):
        try:
            openui = await completer(prompt)
        except Exception:  # noqa: BLE001
            if attempt >= max_attempts:
                raise
            logger.info(
                "generate_ui: generation attempt %d/%d failed; retrying",
                attempt,
                max_attempts,
                exc_info=True,
            )
        else:
            if (openui or "").strip():
                if attempt > 1:
                    logger.info(
                        "generate_ui: generation succeeded on attempt %d/%d",
                        attempt,
                        max_attempts,
                    )
                return str(openui)
            if attempt >= max_attempts:
                return str(openui or "")
            logger.info(
                "generate_ui: generation returned blank output on attempt %d/%d; retrying",
                attempt,
                max_attempts,
            )

        await asyncio.sleep(_GENERATION_RETRY_DELAY_SECONDS * attempt)

    return ""


async def _generate_ui_handler(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
    user_id = ctx.user_id
    request = args.get("request")
    data = args.get("data")
    if not request or not str(request).strip():
        return ToolResult(content="generate_ui: 'request' is required", is_error=True)

    # The tool request is authored by the agent and therefore cannot grant its
    # own permission to expand the user's scope. Gate against the persisted
    # current user message so "列出..." cannot silently become a dashboard.
    try:
        messages = (
            await kernel_client.list_messages(user_id, ctx.session_id, limit=1)
            if ctx.session_id
            else []
        )
    except Exception:  # noqa: BLE001
        logger.debug("generate_ui: user-intent lookup failed", exc_info=True)
        messages = []
    latest = messages[0] if messages else None
    user_message = getattr(latest, "user_message", None)
    user_text = getattr(user_message, "text", "")
    if not isinstance(user_text, str) or not _EXPLICIT_VISUAL_REQUEST_RE.search(user_text):
        return ToolResult(
            content=(
                "generate_ui: the current user message did not explicitly request "
                "a chart, dashboard, visualization, or interactive UI"
            ),
            is_error=True,
        )

    source = await kernel_client.get_session(user_id, ctx.session_id) if ctx.session_id else None
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
        return ToolResult(content=f"generate_ui: model channel unavailable ({exc})", is_error=True)

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
        openui = await _complete_with_retries(
            completer,
            build_openui_prompt(str(request), data),
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("generate_ui: generation failed", exc_info=True)
        return ToolResult(content=f"generate_ui: generation failed ({exc})", is_error=True)

    openui = (openui or "").strip()
    if not openui:
        return ToolResult(content="generate_ui: model returned no OpenUI Lang", is_error=True)
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
