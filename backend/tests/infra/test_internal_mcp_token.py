"""Regression: the internal-MCP shared secret must be STABLE across restarts.

Sessions bake ``settings.internal_mcp_token`` into their stored ``mcp_servers``
``X-Valuz-Internal`` header, and the recovery/resume path replays those stored
sessions. A per-process RANDOM token (the old behaviour) 403'd every pre-restart
session's internal-MCP calls (harness/docs/automations/connectors) — the lead
then reported "no orchestration tools". The token is now DERIVED from the stable
local owner id so it survives restarts, with ``internal_mcp_token_override`` still
winning.
"""

from __future__ import annotations

import hashlib

from valuz_agent.infra.config import settings
from valuz_agent.infra.local_identity import resolve_local_user_id

_NS = b"valuz-internal-mcp\x00"


def _expected(owner: str) -> str:
    return hashlib.sha256(_NS + owner.encode("utf-8")).hexdigest()


def test_token_is_derived_from_owner(monkeypatch) -> None:
    monkeypatch.setattr(settings, "internal_mcp_token_override", None)
    monkeypatch.setattr(
        "valuz_agent.infra.local_identity.resolve_local_user_id", lambda: "local-abc123"
    )
    assert settings.internal_mcp_token == _expected("local-abc123")


def test_token_survives_simulated_restart(monkeypatch) -> None:
    """The actual bug: a fresh process must produce the SAME token (no
    randomness), so a session baked before a restart still authenticates."""
    monkeypatch.setattr(settings, "internal_mcp_token_override", None)
    t1 = settings.internal_mcp_token
    # Simulate a brand-new process: drop the cached owner id and re-resolve.
    resolve_local_user_id.cache_clear()
    t2 = settings.internal_mcp_token
    assert t1 == t2  # would differ under the old per-boot secrets.token_urlsafe(24)


def test_token_differs_per_owner(monkeypatch) -> None:
    monkeypatch.setattr(settings, "internal_mcp_token_override", None)
    monkeypatch.setattr(
        "valuz_agent.infra.local_identity.resolve_local_user_id", lambda: "local-aaa"
    )
    a = settings.internal_mcp_token
    monkeypatch.setattr(
        "valuz_agent.infra.local_identity.resolve_local_user_id", lambda: "local-bbb"
    )
    b = settings.internal_mcp_token
    assert a != b


def test_override_takes_precedence(monkeypatch) -> None:
    monkeypatch.setattr(
        "valuz_agent.infra.local_identity.resolve_local_user_id", lambda: "local-abc123"
    )
    monkeypatch.setattr(settings, "internal_mcp_token_override", "PINNED-VALUE")
    assert settings.internal_mcp_token == "PINNED-VALUE"
