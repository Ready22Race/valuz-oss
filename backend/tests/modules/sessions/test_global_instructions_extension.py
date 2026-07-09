"""Tests for the global-instructions extension seam.

Proves: the OSS default binds no override (empty preamble); a bound provider's
text flows through ``_global_instructions_preamble`` and lands as the FIRST
prompt section, ahead of the agent's own instructions; an override returning
``None``/empty adds nothing (the empty section is skipped by
``assemble_session_instructions``).
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src/app
from __future__ import annotations

from collections.abc import Iterator

import pytest

import valuz_agent.boot.kernel  # noqa: F401 — kernel sys.path side-effect
from valuz_agent.adapters.system_prompt_builder import assemble_session_instructions
from valuz_agent.modules.sessions.service import _global_instructions_preamble
from valuz_agent.ports.extensions import Extensions, ext


@pytest.fixture
def restore_override() -> Iterator[None]:
    original = ext.global_instructions
    try:
        yield
    finally:
        ext.global_instructions = original


class _StaticProvider:
    def __init__(self, text: str | None) -> None:
        self._text = text

    async def global_instructions(self) -> str | None:
        return self._text


def test_oss_default_binds_no_override() -> None:
    assert Extensions().global_instructions is None


async def test_unbound_override_yields_empty_preamble(restore_override: None) -> None:
    ext.global_instructions = None
    assert await _global_instructions_preamble() == ""


async def test_bound_provider_text_flows_through(restore_override: None) -> None:
    ext.global_instructions = _StaticProvider("platform policy: be terse")
    assert await _global_instructions_preamble() == "platform policy: be terse"


async def test_none_result_coerces_to_empty(restore_override: None) -> None:
    ext.global_instructions = _StaticProvider(None)
    assert await _global_instructions_preamble() == ""


async def test_preamble_lands_first_in_assembled_prompt(restore_override: None) -> None:
    ext.global_instructions = _StaticProvider("org compliance preamble")
    prompt = assemble_session_instructions(
        [
            ("global-instructions", await _global_instructions_preamble()),
            ("agent-instructions", "dig deep"),
        ]
    )
    expected_head = "<global-instructions>\norg compliance preamble\n</global-instructions>"
    assert prompt.startswith(expected_head)
    assert prompt.index("global-instructions") < prompt.index("agent-instructions")


async def test_empty_preamble_emits_no_section(restore_override: None) -> None:
    ext.global_instructions = None
    prompt = assemble_session_instructions(
        [
            ("global-instructions", await _global_instructions_preamble()),
            ("agent-instructions", "dig deep"),
        ]
    )
    assert "global-instructions" not in prompt
    assert prompt.startswith("<agent-instructions>")
