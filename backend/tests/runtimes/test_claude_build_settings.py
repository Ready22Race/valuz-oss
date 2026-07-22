"""``_build_settings`` merges harness defaults without clobbering the project.

Each harness default is injected only when the workspace's own
``.claude/settings.json`` hasn't set the key, so an explicit project value
always wins. ``skipWebFetchPreflight`` is additionally gated on the
``VALUZ_SKIP_WEBFETCH_PREFLIGHT`` env var: the CLI's WebFetch preflight
(``api.anthropic.com/api/web/domain_info``) fails closed when Anthropic is
unreachable, so deployments behind restrictive egress opt in to skipping it;
everyone else keeps Anthropic's malicious-domain blocklist.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede `from src.*`
from __future__ import annotations

import json
from pathlib import Path

# Side-effect import: puts the kernel ``src/`` on sys.path before any ``from
# src.*`` below resolves. Mirrors tests/runtimes/test_claude_buffer_size.py.
import kernel  # noqa: F401

import pytest

from src.runtimes.claude_agent.runtime import (
    SKIP_WEBFETCH_PREFLIGHT_ENV,
    ClaudeAgentRuntime,
)


def _build(workspace_root: str | None) -> dict:
    rt = object.__new__(ClaudeAgentRuntime)
    rt.workspace_root = workspace_root
    raw = rt._build_settings()
    return json.loads(raw) if raw is not None else {}


def _write_project_settings(tmp_path: Path, settings: dict) -> str:
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    return str(tmp_path)


# -- baseline: the workflows default ----------------------------------------


def test_workflows_default_injected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(SKIP_WEBFETCH_PREFLIGHT_ENV, raising=False)
    settings = _build(None)
    assert settings == {"enableWorkflows": True}


def test_project_explicit_workflows_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(SKIP_WEBFETCH_PREFLIGHT_ENV, raising=False)
    root = _write_project_settings(tmp_path, {"enableWorkflows": False})
    settings = _build(root)
    assert "enableWorkflows" not in settings  # project value loads via setting_sources


# -- skipWebFetchPreflight: env-gated ---------------------------------------


def test_preflight_skip_not_injected_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(SKIP_WEBFETCH_PREFLIGHT_ENV, raising=False)
    assert "skipWebFetchPreflight" not in _build(None)


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", " 1 "])
def test_preflight_skip_injected_when_env_truthy(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(SKIP_WEBFETCH_PREFLIGHT_ENV, value)
    settings = _build(None)
    assert settings["skipWebFetchPreflight"] is True
    assert settings["enableWorkflows"] is True  # both defaults coexist


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off"])
def test_preflight_skip_ignores_falsy_env(value: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SKIP_WEBFETCH_PREFLIGHT_ENV, value)
    assert "skipWebFetchPreflight" not in _build(None)


def test_project_explicit_preflight_value_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A project that explicitly KEEPS the safety check must not be overridden
    # by the deployment-wide env opt-in.
    monkeypatch.setenv(SKIP_WEBFETCH_PREFLIGHT_ENV, "1")
    root = _write_project_settings(tmp_path, {"skipWebFetchPreflight": False})
    assert "skipWebFetchPreflight" not in _build(root)
