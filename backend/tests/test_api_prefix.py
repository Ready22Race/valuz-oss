"""``create_app(api_prefix=...)`` route-mounting behaviour.

Inspects the route table built by ``create_app()`` (no DB needed): the whole
public HTTP surface — host routers, overlay ``module_registry`` routes, and the
in-process kernel routers — is mounted under each configured prefix, while the
internal ``/internal/mcp/*`` ASGI mounts stay at their fixed native paths
(they're reached server-side via ``backend_base_url``, never the ingress).
"""

from __future__ import annotations

import pytest
from fastapi.routing import APIRoute
from starlette.routing import Mount

from valuz_agent.api.app import create_app
from valuz_agent.infra.config import Settings, settings

# A representative, parameter-free host route — the one that 404'd behind the
# shared-host ingress when the seam was missing.
_HOST_PATH = "/v1/decisions/pending"
# A representative in-process kernel route (native prefix /api/v1/*).
_KERNEL_PATH = "/api/v1/sessions"
# Internal ASGI mounts that must never move under a prefix.
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


def test_internal_mcp_mounts_are_never_prefixed() -> None:
    """The /internal/mcp/* mounts stay fixed regardless of the prefix."""
    app = create_app(api_prefix=["/valuz-backend"])
    mounts = _mount_paths(app)

    assert _MCP_MOUNT in mounts
    assert "/valuz-backend" + _MCP_MOUNT not in mounts


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
