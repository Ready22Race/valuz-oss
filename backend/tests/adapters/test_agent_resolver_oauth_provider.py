"""``_resolve_agent_provider`` must stay quiet on the OAuth-subscription path.

An agent pinned to a subscription provider (``auth_type="oauth"`` — codex/claude
``/login``) resolves to ``None`` ModelProvider by design: there's no API key to
forward, the CLI supplies the credential out-of-band. ``resolve_model_provider``
returns ``None`` *without raising* only on this path (every genuine failure
raises ``ProviderNotResolvable``). The resolver must therefore treat that ``None``
as healthy — return env-fallback ``None`` and emit NO warning. Regression guard
against the false-alarm WARNING that fired on every subscription session.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

import logging

import valuz_agent.boot.kernel  # noqa: F401 — kernel sys.path side-effect

from src.core import AgentConfig

from valuz_agent.adapters.agent_resolver import _resolve_agent_provider


class _FakeOAuthProvider:
    id = "ch-codex-subscription"
    name = "Codex · ChatGPT"
    enabled = True
    auth_type = "oauth"
    provider_kind = "codex-subscription"


class _FakeProviderDatastore:
    async def get_by_id(self, user_id: str, provider_id: str) -> _FakeOAuthProvider | None:
        return _FakeOAuthProvider() if provider_id == "ch-codex-subscription" else None


async def test_oauth_subscription_resolves_to_none_without_warning(
    caplog: object,
) -> None:
    agent = AgentConfig(
        id="agent:整理员",
        name="整理员",
        runtime_provider="codex",
        model="gpt-5.5",
        metadata={"provider_id": "ch-codex-subscription"},
    )

    with caplog.at_level(logging.DEBUG, logger="valuz_agent.adapters.agent_resolver"):  # type: ignore[attr-defined]
        resolved = await _resolve_agent_provider(
            agent=agent,
            model="gpt-5.5",
            providers=_FakeProviderDatastore(),
        )

    # Healthy oauth path: env fallback, no ModelProvider.
    assert resolved is None
    # No WARNING (or worse) — the old code logged a misleading
    # "provider row may be missing/disabled" warning here.
    warnings = [
        r
        for r in caplog.records  # type: ignore[attr-defined]
        if r.name == "valuz_agent.adapters.agent_resolver" and r.levelno >= logging.WARNING
    ]
    assert warnings == [], [r.getMessage() for r in warnings]
