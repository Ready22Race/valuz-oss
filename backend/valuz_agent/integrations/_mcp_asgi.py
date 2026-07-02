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
    """Resolve the session owner from the raw session id (durable, cross-owner)."""
    from valuz_agent.adapters.data_reader import data_reader

    try:
        sessions = await data_reader().list_all_sessions(ids=[session_id], limit=1)
    except Exception:  # noqa: BLE001 — owner resolution is best-effort
        logger.warning(
            "Internal MCP: failed resolving owner for session %s", session_id, exc_info=True
        )
        return None
    return sessions[0].user_id if sessions else None


def _verify_token_owner(token: str | None) -> str | None:
    """Verified owner from a per-owner MCP token, or None if invalid/absent.

    Same per-owner signing/verification as the data service (unifies the two
    forms — see ADR-012): the token's ``sub`` picks the owner's secret, the
    signature proves it. A forged ``sub`` / unknown owner fails.
    """
    if not token:
        return None
    from src.core.token_signer import InvalidTokenError

    from valuz_agent.boot.kernel import make_host_data_service_verifier_per_owner

    try:
        claims = make_host_data_service_verifier_per_owner().verify(token)
    except InvalidTokenError:
        return None
    return claims.user_id if claims else None


def build_internal_mcp_asgi(inner: Any) -> Any:
    """Return a wrapper ASGI app for built-in MCP endpoints.

    The wrapper enforces (per-owner, both forms — ADR-012):
      1) ``X-Valuz-Internal`` carries a per-owner signed token → verified owner
      2) `X-Valuz-Session-Id` presence
      3) the session belongs to the verified owner (anti cross-owner)
      4) built-in MCP context publication for request-scoped access
    """

    async def _app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            response = PlainTextResponse("Not Found", status_code=404)
            await response(scope, receive, send)
            return

        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []
        }
        # Owner comes from the VERIFIED token — never a shared secret or a trusted
        # header. A forged sub / unknown owner fails verification.
        owner_id = _verify_token_owner(headers.get("x-valuz-internal"))
        if not owner_id:
            response = PlainTextResponse("Forbidden", status_code=403)
            await response(scope, receive, send)
            return

        session_id = headers.get("x-valuz-session-id") or ""
        if not session_id:
            response = PlainTextResponse("Missing X-Valuz-Session-Id header", status_code=400)
            await response(scope, receive, send)
            return

        # The session must belong to the authenticated owner (cross-owner guard).
        session_owner = await _resolve_session_owner(session_id)
        if session_owner != owner_id:
            response = PlainTextResponse("Forbidden", status_code=403)
            await response(scope, receive, send)
            return

        mcp_ctx_token = set_current_mcp_context(session_id=session_id, user_id=owner_id)
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
