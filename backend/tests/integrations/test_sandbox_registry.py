"""Tests for the sandbox driver registry — the OSS pluggability seam.

Proves: the built-in ``seatbelt`` driver is registered; an overlay can register
its own driver and resolve it by name (zero OSS edits); unknown/None resolve to
None (→ in-process); and the Seatbelt preflight gates on macOS version.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src/app
from __future__ import annotations

import sys

import pytest

import valuz_agent.boot.kernel  # noqa: F401
from valuz_agent.integrations import sandbox_registry
from valuz_agent.ports.sandbox_provider import (
    SandboxBootContext,
    SandboxBootResult,
    SandboxEndpoint,
)


def test_builtin_seatbelt_is_registered() -> None:
    assert "seatbelt" in sandbox_registry.available()
    d = sandbox_registry.get("seatbelt")
    assert d is not None and d.name == "seatbelt"


def test_unset_and_unknown_resolve_to_none() -> None:
    assert sandbox_registry.get(None) is None
    assert sandbox_registry.get("") is None
    assert sandbox_registry.get("does-not-exist") is None


class _FakeDriver:
    """A stand-in for an overlay-supplied cloud driver."""

    name = "fake-cloud"

    def preflight(self) -> list[str]:
        return []

    async def provision_for_boot(self, ctx: SandboxBootContext) -> SandboxBootResult:
        ep = SandboxEndpoint("host-kernel", "http://127.0.0.1:1", "tok")
        return SandboxBootResult(endpoint=ep, provider=object(), static_roots=())  # type: ignore[arg-type]

    def attach(self, ctx: SandboxBootContext, endpoint: SandboxEndpoint) -> SandboxBootResult:
        return SandboxBootResult(endpoint=endpoint, provider=object(), static_roots=())  # type: ignore[arg-type]


def test_overlay_can_register_and_resolve(monkeypatch) -> None:
    """The whole point: an overlay registers a driver and dispatch resolves it
    by name — without OSS knowing the concrete type."""
    monkeypatch.setitem(sandbox_registry._drivers, "fake-cloud", _FakeDriver())
    d = sandbox_registry.get("fake-cloud")
    assert d is not None and d.name == "fake-cloud"
    # seatbelt is still there alongside it.
    assert "seatbelt" in sandbox_registry.available()


# ---- Seatbelt preflight version gate ----------------------------------


@pytest.mark.skipif(sys.platform != "darwin", reason="version gate is macOS-only")
def test_seatbelt_preflight_clean_on_supported_macos() -> None:
    from valuz_agent.integrations.sandbox_seatbelt import seatbelt_preflight

    # The dev/CI macOS host is above the floor → no version problem.
    assert not any("below the supported floor" in p for p in seatbelt_preflight())


@pytest.mark.skipif(sys.platform != "darwin", reason="version gate is macOS-only")
def test_seatbelt_preflight_gates_below_floor(monkeypatch) -> None:
    from valuz_agent.integrations.sandbox_seatbelt import seatbelt_preflight

    # An absurdly high floor makes any real macOS "unsupported".
    monkeypatch.setenv("VALUZ_SEATBELT_MIN_MACOS", "99")
    problems = seatbelt_preflight()
    assert any("below the supported floor" in p for p in problems)


def test_seatbelt_driver_preflight_off_macos(monkeypatch) -> None:
    from valuz_agent.integrations import sandbox_seatbelt as sb

    monkeypatch.setattr(sb.sys, "platform", "linux")
    d = sandbox_registry.get("seatbelt")
    assert d is not None
    assert any("not macOS" in p for p in d.preflight())
