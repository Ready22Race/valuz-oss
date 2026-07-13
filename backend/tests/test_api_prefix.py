"""``create_app(api_prefix=...)`` route-mounting behaviour.

Inspects the route table built by ``create_app()`` (no DB needed): the whole
public HTTP surface — host routers, overlay ``module_registry`` routes, and the
in-process kernel routers — plus the internal ``/internal/data`` +
``/internal/mcp/*`` ASGI sub-apps are mounted under each configured base path, so
a kernel reaching the host through the prefixed ingress (a cloud sandbox) can
resolve ``{backend_base_url}/internal/*`` too.
"""

from __future__ import annotations

import pytest
from fastapi.routing import APIRoute
from starlette.routing import Mount

from valuz_agent.api.app import create_app
from valuz_agent.infra.config import Settings, settings

# A representative, parameter-free host route — the one that 404'd behind the
# shared-host ingress when the seam was missing.
_HOST_PATH = "/v1/notifications"
# A representative in-process kernel route (native prefix /api/v1/*).
_KERNEL_PATH = "/api/v1/sessions"
# A representative internal ASGI mount — now mounted under each base path too.
_MCP_MOUNT = "/internal/mcp/docs"


def _api_paths(app) -> set[str]:
    return {r.path for r in app.routes if isinstance(r, APIRoute)}


def _mount_paths(app) -> set[str]:
    return {r.path for r in app.routes if isinstance(r, Mount)}


def test_default_is_a_noop() -> None:
    """No argument (and empty settings) → routes served at native paths."""
    paths = _api_paths(create_app())
    assert _HOST_PATH in paths
    assert _KERNEL_PATH in paths
    assert _MCP_MOUNT in _mount_paths(create_app())


def test_single_prefix_shifts_the_whole_surface() -> None:
    """One prefix moves host + kernel routes; the bare paths stop being served."""
    paths = _api_paths(create_app(api_prefix=["/valuz-backend"]))

    assert "/valuz-backend" + _HOST_PATH in paths
    assert "/valuz-backend" + _KERNEL_PATH in paths
    assert _HOST_PATH not in paths
    assert _KERNEL_PATH not in paths


def test_internal_mounts_follow_each_base_path() -> None:
    """Internal sub-apps (``/internal/data`` + ``/internal/mcp/*``) mount under
    EACH configured base path — so a kernel whose ``backend_base_url`` carries the
    ingress sub-path (a cloud sandbox reachable only through it) resolves them
    too. Updated from the old root-only contract."""
    # prefix-only → the internal mounts live under that prefix.
    mounts = _mount_paths(create_app(api_prefix=["/valuz-backend"]))
    assert "/valuz-backend" + _MCP_MOUNT in mounts
    assert "/valuz-backend/internal/data" in mounts

    # native + prefixed → served at BOTH, so internal ``backend_base_url`` callers
    # keep resolving the root mounts while the prefixed ingress exposes them too.
    both = _mount_paths(create_app(api_prefix=["", "/valuz-backend"]))
    assert _MCP_MOUNT in both and "/valuz-backend" + _MCP_MOUNT in both
    assert "/internal/data" in both and "/valuz-backend/internal/data" in both


def test_dual_mount_serves_native_and_prefixed() -> None:
    """``["", "/valuz-backend"]`` → the surface is served at BOTH paths.

    This is the shared-backend deploy shape (env ``VALUZ_API_PREFIX=,/valuz-backend``):
    native paths keep internal/probe callers working while the ingress sees the
    prefixed surface.
    """
    paths = _api_paths(create_app(api_prefix=["", "/valuz-backend"]))

    assert _HOST_PATH in paths
    assert "/valuz-backend" + _HOST_PATH in paths
    assert "/valuz-backend" + _KERNEL_PATH in paths


def test_none_falls_back_to_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """``api_prefix=None`` (default) → uses ``settings.api_prefix``."""
    monkeypatch.setattr(settings, "api_prefix", ["/valuz-backend"])
    paths = _api_paths(create_app())

    assert "/valuz-backend" + _HOST_PATH in paths
    assert _HOST_PATH not in paths


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", []),
        ("/valuz-backend", ["/valuz-backend"]),
        ("valuz-backend", ["/valuz-backend"]),
        ("/valuz-backend/", ["/valuz-backend"]),
        ("/a,/b", ["/a", "/b"]),
        (" /a , b/ ", ["/a", "/b"]),
        (",/valuz-backend", ["", "/valuz-backend"]),  # leading comma → native + prefix
        (["/a", "/a", "/b"], ["/a", "/b"]),  # dedup, order preserved
        (["", "/gw"], ["", "/gw"]),
    ],
)
def test_prefix_is_parsed_and_normalized(raw: object, expected: list[str]) -> None:
    """Accepts a comma-separated string or a list; normalises + dedups entries."""
    assert Settings(api_prefix=raw).api_prefix == expected
