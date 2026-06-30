"""Shared ASGI wrapper for in-process built-in MCP endpoints."""

from __future__ import annotations

import logging
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

from starlette.responses import PlainTextResponse

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BuiltinMCPContext:
    session_id: str
    user_id: str


_mcp_context: ContextVar[BuiltinMCPContext | None] = ContextVar(
    "valuz_internal_builtin_mcp_context", default=None
)


def get_current_mcp_context() -> BuiltinMCPContext:
    ctx = _mcp_context.get()
    if ctx is None:
        raise RuntimeError("MCP context unavailable: request is not MCP-scoped")
    return ctx


def get_current_mcp_session_id() -> str:
    return get_current_mcp_context().session_id


def get_current_mcp_user_id() -> str:
    return get_current_mcp_context().user_id


def set_current_mcp_context(*, session_id: str, user_id: str) -> Token[BuiltinMCPContext | None]:
    return _mcp_context.set(BuiltinMCPContext(session_id=session_id, user_id=user_id))


def reset_current_mcp_context(token: Token[BuiltinMCPContext | None]) -> None:
    _mcp_context.reset(token)


async def _resolve_session_owner(session_id: str) -> str | None:
    """Resolve the session owner from the raw session id."""
    from valuz_agent.adapters import kernel_client

    try:
        sessions = await kernel_client.list_all_sessions(ids=[session_id], limit=1)
    except Exception:  # noqa: BLE001 — owner resolution is best-effort
        logger.warning(
            "Internal MCP: failed resolving owner for session %s", session_id, exc_info=True
        )
        return None
    return sessions[0].user_id if sessions else None


def build_internal_mcp_asgi(inner: Any) -> Any:
    """Return a wrapper ASGI app for built-in MCP endpoints.

    The wrapper enforces:
      1) per-process secret header
      2) `X-Valuz-Session-Id` presence
      3) owner resolution from kernel session
      4) built-in MCP context publication for request-scoped access
    """

    async def _app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            response = PlainTextResponse("Not Found", status_code=404)
            await response(scope, receive, send)
            return

        from valuz_agent.infra.config import settings as _settings

        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []
        }
        if headers.get("x-valuz-internal") != _settings.internal_mcp_token:
            response = PlainTextResponse("Forbidden", status_code=403)
            await response(scope, receive, send)
            return

        session_id = headers.get("x-valuz-session-id") or ""
        if not session_id:
            response = PlainTextResponse("Missing X-Valuz-Session-Id header", status_code=400)
            await response(scope, receive, send)
            return

        user_id = await _resolve_session_owner(session_id)
        if not user_id:
            response = PlainTextResponse("Unknown session owner", status_code=401)
            await response(scope, receive, send)
            return

        mcp_ctx_token = set_current_mcp_context(session_id=session_id, user_id=user_id)
        try:
            await inner(scope, receive, send)
        finally:
            reset_current_mcp_context(mcp_ctx_token)

    return _app


__all__ = [
    "BuiltinMCPContext",
    "build_internal_mcp_asgi",
    "get_current_mcp_context",
    "get_current_mcp_session_id",
    "get_current_mcp_user_id",
    "set_current_mcp_context",
    "reset_current_mcp_context",
]
