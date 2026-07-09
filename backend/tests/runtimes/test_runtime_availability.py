"""Kernel runtime-availability probe (design §3.3).

The kernel owns the runtime binaries; ``probe_runtime_availability`` is the
source of truth the host reads via ``KernelClient.runtime_availability``.
"""

from __future__ import annotations

from unittest.mock import patch

from src.runtimes.availability import probe_runtime_availability


def test_pure_python_runtimes_always_available() -> None:
    out = probe_runtime_availability()
    assert out["claude_agent"] == {"available": True, "unavailable_reason": None}
    assert out["deepagents"] == {"available": True, "unavailable_reason": None}


def test_codex_available_via_env_override(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_BIN_OVERRIDE", "/opt/bin/codex")
    with patch("src.runtimes.availability.shutil.which", return_value="/opt/bin/codex"):
        out = probe_runtime_availability()
    assert out["codex"] == {"available": True, "unavailable_reason": None}


def test_codex_unavailable_when_binary_missing(monkeypatch) -> None:
    monkeypatch.delenv("CODEX_BIN_OVERRIDE", raising=False)
    with (
        patch("src.runtimes.availability._bundled_codex_path", return_value=None),
        patch("src.runtimes.availability.shutil.which", return_value=None),
    ):
        out = probe_runtime_availability()
    assert out["codex"]["available"] is False
    assert "codex" in out["codex"]["unavailable_reason"]


def test_codex_available_via_bundled_binary(monkeypatch) -> None:
    # bundled by codex_cli_bin but not on PATH → still available.
    monkeypatch.delenv("CODEX_BIN_OVERRIDE", raising=False)
    with (
        patch("src.runtimes.availability._bundled_codex_path", return_value="/bundled/codex"),
        patch("src.runtimes.availability.shutil.which", return_value=None),
    ):
        out = probe_runtime_availability()
    assert out["codex"] == {"available": True, "unavailable_reason": None}
