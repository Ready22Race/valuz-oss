"""ChatAnthropic ``max_tokens`` resolution for the deepagents runtime.

langchain-anthropic fills an unset ``max_tokens`` from a bundled per-model
profile registry keyed by EXACT model name; any name it doesn't know —
gateway aliases like ``openrouter/claude-sonnet-4-5`` or anthropic-compatible
third-party models — silently falls back to 4096, and long answers truncate
with ``stop_reason: max_tokens``. The runtime works around this via
``_resolve_anthropic_max_tokens``; these tests pin both the workaround and
the private langchain surface it leans on.
"""

# ruff: noqa: I001
from __future__ import annotations

import valuz_agent.boot.kernel  # noqa: F401  (puts kernel `src` on the import path)

from src.runtimes.deepagents.runtime import (
    _ANTHROPIC_UNKNOWN_MODEL_MAX_TOKENS,
    _resolve_anthropic_max_tokens,
)


def test_known_model_defers_to_langchain_profile():
    """Registry hit → ``None`` → ChatAnthropic applies its own per-model
    default (64k for sonnet-4-x, 128k for opus-4-6+, …)."""
    assert _resolve_anthropic_max_tokens("claude-sonnet-4-5") is None
    assert _resolve_anthropic_max_tokens("claude-opus-4-6") is None
    assert _resolve_anthropic_max_tokens("claude-haiku-4-5") is None


def test_gateway_prefixed_alias_uses_upstream_profile_cap():
    """``vendor/claude-…`` aliases resolve to the upstream model's cap, not
    the generic fallback."""
    from langchain_anthropic.chat_models import _get_default_model_profile

    expected = _get_default_model_profile("claude-sonnet-4-5")["max_output_tokens"]
    assert _resolve_anthropic_max_tokens("openrouter/claude-sonnet-4-5") == expected


def test_unknown_model_gets_safe_cap_not_4096():
    resolved = _resolve_anthropic_max_tokens("my-custom-gateway-model")
    assert resolved == _ANTHROPIC_UNKNOWN_MODEL_MAX_TOKENS
    assert resolved > 4096


def test_chat_anthropic_construction_lands_above_langchain_floor():
    """End-to-end: constructing ChatAnthropic the way the runtime does no
    longer lands on langchain's 4096 floor for unknown names."""
    from langchain_anthropic import ChatAnthropic
    from pydantic import SecretStr

    kwargs: dict = dict(
        api_key=SecretStr("sk-fake"),
        model_name="my-custom-gateway-model",
        timeout=None,
        stop=None,
    )
    max_tokens = _resolve_anthropic_max_tokens("my-custom-gateway-model")
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    chat = ChatAnthropic(**kwargs)
    assert chat.max_tokens == _ANTHROPIC_UNKNOWN_MODEL_MAX_TOKENS


def test_private_langchain_profile_helper_still_exists():
    """Canary: the workaround leans on a private langchain-anthropic helper.
    If an upgrade moves it, ``_resolve_anthropic_max_tokens`` degrades to
    returning ``None`` for everything (pre-fix behavior: unknown names fall
    back to 4096). Fail here so the upgrade PR revisits the workaround
    instead of shipping the regression silently."""
    from langchain_anthropic.chat_models import _get_default_model_profile

    assert _get_default_model_profile("claude-sonnet-4-5").get("max_output_tokens")
    # Unknown names must return an empty profile (not raise) — the resolver
    # relies on ``.get()`` miss semantics.
    assert _get_default_model_profile("definitely-not-a-model") == {}
