"""Generative-UI ephemeral-session completer — the LLM-call seam.

Mirrors ``modules/memory/runner.py::_make_completer``: a throwaway no-tools
kernel session cloning the calling session's resolved runtime/provider/model,
one ``run_turn`` returning OpenUI Lang, then delete + rmtree. Best-effort by
contract — failures bubble to the tool handler, which converts them to an
error result without affecting the originating turn.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

import valuz_agent.boot.kernel  # noqa: F401  (sets kernel import path)
from valuz_agent.adapters import kernel_client
from valuz_agent.infra.fs_registry import fs_registry
from valuz_agent.modules.genui.prompts import GENERATIVE_UI_INSTRUCTIONS

logger = logging.getLogger(__name__)

Completer = Callable[[str], Awaitable[str]]


def _resolve_provider_id(source: Any) -> str | None:
    """Provider id for the ephemeral session: prefer the host-stamped
    ``valuz.locked_provider_id`` (chat/project), fall back to the embedded
    agent config's ``metadata.provider_id`` (task lead)."""
    valuz = (getattr(source, "metadata", None) or {}).get("valuz", {}) or {}
    pid = valuz.get("locked_provider_id")
    if pid:
        return str(pid)
    ac = getattr(source, "agent_config", None)
    meta = (getattr(ac, "metadata", None) or {}) if ac is not None else {}
    pid = meta.get("provider_id")
    return str(pid) if pid else None


def _make_completer(
    *,
    user_id: str,
    runtime_provider: Any,
    model: str,
    mp: Any,
    calling_session_id: str | None = None,
    tool_use_id: str | None = None,
) -> Completer:
    """Build the ``complete`` seam backed by a throwaway no-tools kernel session
    cloning the source's runtime/provider/model. Each call is a fresh ephemeral
    session (deleted after), sharing ONE fixed scratch cwd
    (``FsRegistry.generative_ui_cwd``).

    When ``calling_session_id`` + ``tool_use_id`` are set, the ephemeral
    session's ``text_delta`` stream is forwarded to the CALLING session as
    ``tool_output_delta`` (keyed by ``tool_use_id``) via the existing
    ``kernel_client.emit_live_event`` live-injection channel, so the frontend
    ``<Renderer isStreaming>`` paints progressively. ``run_turn`` still returns
    the full text as the canonical ToolResult. When either is None, behaves as
    the synchronous (non-streaming) version."""

    async def _forward_deltas(ephem_id: str) -> None:
        forwarded = 0
        try:
            async for ev in kernel_client.subscribe_session_events(user_id, ephem_id):
                if getattr(ev, "type", None) != "text_delta":
                    continue
                text = (getattr(ev, "data", None) or {}).get("text")
                if not text:
                    continue
                await kernel_client.emit_live_event(
                    user_id,
                    calling_session_id or "",
                    "tool_output_delta",
                    {"id": tool_use_id, "text": text},
                )
                forwarded += 1
                logger.debug(
                    "generate_ui: forwarded delta #%d (%d chars) tool_use_id=%s",
                    forwarded,
                    len(text),
                    tool_use_id,
                )
        except asyncio.CancelledError:
            logger.info(
                "generate_ui: delta forwarding cancelled after %d deltas (tool_use_id=%s)",
                forwarded,
                tool_use_id,
            )
            raise
        except Exception:  # noqa: BLE001 — best-effort; canonical full text still wins
            logger.exception(
                "generate_ui: delta forwarding stopped after %d deltas (tool_use_id=%s)",
                forwarded,
                tool_use_id,
            )
        else:
            logger.info(
                "generate_ui: streamed %d deltas for tool_use_id=%s",
                forwarded,
                tool_use_id,
            )

    async def _complete(prompt: str) -> str:
        from app.schemas import AgentConfigSchema, CreateSessionRequest, ModelProviderInputSchema

        # OAuth/subscription channels (Codex/Claude login) resolve to mp=None and
        # carry no static key — create the session with model_provider=None so the
        # runtime self-authenticates, exactly like the source session.
        mp_schema = (
            ModelProviderInputSchema(
                base_url=mp.base_url, api_key=mp.api_key, api_protocol=mp.api_protocol
            )
            if (mp is not None and getattr(mp, "api_key", None))
            else None
        )
        ephem_id = uuid4().hex
        gen_cwd = fs_registry.generative_ui_cwd(user_id)
        marker = {"valuz": {"ephemeral_generative_ui": True}}
        req = CreateSessionRequest(
            id=ephem_id,
            agent_config=AgentConfigSchema(
                name="generative-ui",
                model=model,
                runtime_provider=runtime_provider,
                instructions=GENERATIVE_UI_INSTRUCTIONS,
                metadata=marker,
            ),
            cwd=str(gen_cwd),
            runtime_provider=runtime_provider,
            model=model,
            model_provider=mp_schema,
            instructions=GENERATIVE_UI_INSTRUCTIONS,
            permission_mode="default",
            metadata=marker,
        )
        await kernel_client.create_session(user_id, req)
        stream_task: asyncio.Task[None] | None = None
        if calling_session_id and tool_use_id:
            # Subscribe before run_turn: text_delta is live-only and not
            # persisted, so the subscription must be attached before the turn
            # emits. ``sleep(0)`` lets the task begin attaching its tap.
            logger.info(
                "generate_ui: streaming ephem=%s -> calling=%s tool_use_id=%s",
                ephem_id,
                calling_session_id,
                tool_use_id,
            )
            stream_task = asyncio.create_task(_forward_deltas(ephem_id))
            await asyncio.sleep(0)
        try:
            msg = await kernel_client.run_turn(user_id, ephem_id, prompt)
            return msg.assistant_message or ""
        finally:
            if stream_task is not None:
                stream_task.cancel()
                with contextlib.suppress(BaseException):
                    await stream_task
            try:
                await kernel_client.delete_session(user_id, ephem_id)
            except Exception:  # noqa: BLE001
                logger.debug("generative-ui: ephemeral session cleanup failed")

    return _complete
