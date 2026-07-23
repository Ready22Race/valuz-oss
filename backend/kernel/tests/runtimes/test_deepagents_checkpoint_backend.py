"""Checkpoint backend selection for the DeepAgents runtime.

The gate used to be hardcoded to ``_in_sandbox()``. It is now
``_checkpoint_backend()``, which keeps that as the default but lets a
deployment pin the store explicitly with ``DEEPAGENTS_CHECKPOINT_BACKEND``.
These cover the default-preservation contract (so the override cannot silently
change existing installs) and both override directions.
"""

from __future__ import annotations

import pytest

from src.runtimes.deepagents.runtime import (
    CHECKPOINT_BACKEND_ENV,
    _checkpoint_backend,
)


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (CHECKPOINT_BACKEND_ENV, "IS_SANDBOX", "KERNEL_STORE"):
        monkeypatch.delenv(name, raising=False)


def test_defaults_are_unchanged_without_the_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Local resident process → sqlite (historical behaviour).
    assert _checkpoint_backend() == "sqlite"

    # Sandbox image sets IS_SANDBOX → file.
    monkeypatch.setenv("IS_SANDBOX", "1")
    assert _checkpoint_backend() == "file"
    monkeypatch.delenv("IS_SANDBOX")

    # SaaS store tier also implies the ephemeral sandbox → file.
    monkeypatch.setenv("KERNEL_STORE", "remote")
    assert _checkpoint_backend() == "file"


@pytest.mark.parametrize("value", ["file", "FILE", " file "])
def test_override_pins_file_even_when_local(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(CHECKPOINT_BACKEND_ENV, value)
    assert _checkpoint_backend() == "file"


@pytest.mark.parametrize("value", ["sqlite", "SQLite"])
def test_override_pins_sqlite_even_inside_the_sandbox(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("IS_SANDBOX", "1")
    monkeypatch.setenv(CHECKPOINT_BACKEND_ENV, value)
    assert _checkpoint_backend() == "sqlite"


def test_unrecognised_value_falls_back_to_the_gate_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A typo must not take the runtime down — fall back to the deployment gate.
    monkeypatch.setenv(CHECKPOINT_BACKEND_ENV, "postgres")
    assert _checkpoint_backend() == "sqlite"
    monkeypatch.setenv("IS_SANDBOX", "1")
    assert _checkpoint_backend() == "file"
