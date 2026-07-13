"""Generative-UI ephemeral-session completer — the LLM-call seam.

Mirrors ``modules/memory/runner.py::_make_completer``: a throwaway no-tools
kernel session cloning the calling session's resolved runtime/provider/model,
one ``run_turn`` returning OpenUI Lang, then delete + rmtree. Best-effort by
contract — failures bubble to the tool handler, which converts them to an
error result without affecting the originating turn.
"""

from __future__ import annotations

import logging
import shutil
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
    *, user_id: str, runtime_provider: Any, model: str, mp: Any
) -> Completer:
    """Build the ``complete`` seam backed by a throwaway no-tools kernel session
    cloning the source's runtime/provider/model."""

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
        gen_cwd = fs_registry.data_dir(user_id) / "generative-ui" / ephem_id
        gen_cwd.mkdir(parents=True, exist_ok=True)
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
        try:
            msg = await kernel_client.run_turn(user_id, ephem_id, prompt)
            return msg.assistant_message or ""
        finally:
            try:
                await kernel_client.delete_session(user_id, ephem_id)
            except Exception:  # noqa: BLE001
                logger.debug("generative-ui: ephemeral session cleanup failed")
            shutil.rmtree(gen_cwd, ignore_errors=True)

    return _complete
