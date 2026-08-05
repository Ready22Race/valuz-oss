"""``model_context_window`` / ``model_auto_compact_token_limit`` emission.

Codex's own model catalog can't know gateway aliases (``valuz-pro-anthropic``
style ids), so without an override it falls back to its generic context
bookkeeping. When the session carries a channel-declared
``ModelSettings.max_input_tokens``, the runtime emits both keys — the window
itself and the compaction trigger at the shared fraction — as BARE TOML
integers (quoting turns them into strings codex rejects at startup). No
declaration → neither key, so codex keeps its tuned defaults for models it
does know.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from src.core.agent_config import AgentConfig
from src.core.types import ModelSettings, Session
from src.runtimes.codex.runtime import _build_config_overrides


def _session(model_settings: ModelSettings | None) -> Session:
    return Session(
        id="s1",
        agent_config=AgentConfig(id="a", name="a"),
        cwd="/tmp",
        runtime_provider="codex",
        model_settings=model_settings,
    )


def test_declared_window_emits_both_overrides_as_bare_ints() -> None:
    ov = _build_config_overrides(_session(ModelSettings(max_input_tokens=200_000)), None, "alias")
    assert "model_context_window=200000" in ov
    assert "model_auto_compact_token_limit=170000" in ov  # 0.85 x 200k
    # Bare integers — a quoted value is the exact shape codex rejects.
    assert not any('model_context_window="' in o for o in ov)
    assert not any('model_auto_compact_token_limit="' in o for o in ov)


def test_no_declaration_emits_neither_override() -> None:
    for settings in (None, ModelSettings(effort="high")):
        ov = _build_config_overrides(_session(settings), None, "gpt-5.5")
        assert not any(o.startswith("model_context_window=") for o in ov)
        assert not any(o.startswith("model_auto_compact_token_limit=") for o in ov)
