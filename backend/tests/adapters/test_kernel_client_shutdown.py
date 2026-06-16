"""``_store`` / ``_orchestrator`` surface a torn-down kernel as the typed
``KernelUnavailableError`` (not a bare ``RuntimeError``).

Regression for the shutdown race: on app-lifespan exit the kernel's StorePort
singleton is reset to ``None`` (``shutdown_dependencies``). A background actor
loop, cancelled mid-flight, runs its ``finally`` finalize which calls
``kernel_client.get_session`` → ``get_store()`` → ``RuntimeError("Dependencies
not initialized")``. That raw error was logged as a scary ERROR traceback for
every in-flight session. Mapping it to ``KernelUnavailableError`` lets the
best-effort finalize callers skip quietly (boot recovery reconciles the
session). See ``adapters/kernel_client._store`` + ``_finalize_actor``.
"""

from __future__ import annotations

import pytest

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect for ``app.*``
from valuz_agent.adapters import kernel_client


def test_store_maps_uninitialized_to_kernel_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.dependencies as kernel_deps

    def _boom() -> object:
        raise RuntimeError("Dependencies not initialized — is the app lifespan running?")

    monkeypatch.setattr(kernel_deps, "get_store", _boom)
    with pytest.raises(kernel_client.KernelUnavailableError):
        kernel_client._store()


def test_orchestrator_maps_uninitialized_to_kernel_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.dependencies as kernel_deps

    def _boom() -> object:
        raise RuntimeError("Dependencies not initialized — is the app lifespan running?")

    monkeypatch.setattr(kernel_deps, "get_orchestrator", _boom)
    with pytest.raises(kernel_client.KernelUnavailableError):
        kernel_client._orchestrator()


def test_store_passthrough_when_initialized(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.dependencies as kernel_deps

    sentinel = object()
    monkeypatch.setattr(kernel_deps, "get_store", lambda: sentinel)
    assert kernel_client._store() is sentinel
