"""Request-scoped owner id context used for owner-scoped reads/writes."""

from __future__ import annotations

from contextvars import ContextVar, Token

_current_user_id: ContextVar[str | None] = ContextVar("valuz_current_user_id", default=None)


class OwnerContextUnsetError(LookupError):
    """Raised when an owner-scoped read needs a user_id but none is set."""


def get_current_user_id() -> str | None:
    """Read the raw owner id from context (or ``None`` if unset)."""
    return _current_user_id.get()


def set_current_user_id(user_id: str | None) -> Token[str | None]:
    """Set the owner id for the current context; returns a reset token."""
    return _current_user_id.set(user_id)


def reset_current_user_id(token: Token[str | None]) -> None:
    _current_user_id.reset(token)


__all__ = [
    "OwnerContextUnsetError",
    "get_current_user_id",
    "set_current_user_id",
    "reset_current_user_id",
]
