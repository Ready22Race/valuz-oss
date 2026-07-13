"""Regression tests for mcp_resolver header injection.

Guards the "passes the test, fails at runtime" divergence: the connector
probe and the runtime resolver must inject identical headers. Both go through
``service.build_overrides``, which reads the unified ``headers_json``
(``{name: {value, secret}}``). The Authorization header keeps a single
transitional Bearer-prefix compat for *secret* entries (legacy / migrated
tokens stored raw); custom header names carry the raw value verbatim.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

# Side-effect import — surfaces ``src.core...`` on sys.path before the
# resolver imports ``McpServerConfig`` at module load.
import valuz_agent.boot.kernel  # noqa: F401
from valuz_agent.adapters.mcp_resolver import _build_http_config
from valuz_agent.ports.extensions import ext


@dataclass
class _FakeRow:
    id: str = "c1"
    user_id: str = "user-1"
    slug: str = "acme"
    url: str = "https://mcp.acme.test/mcp"
    transport: str = "http"
    auth_type: str = "bearer"
    oauth_token_json: str | None = None
    headers_json: str | None = None
    params_json: str | None = None
    args: str | None = None


def _row(name: str) -> _FakeRow:
    return _FakeRow(headers_json=json.dumps({name: {"value": "sk-123", "secret": True}}))


async def _headers(row: _FakeRow) -> dict[str, str]:
    # ``connectors`` is unused for a non-OAuth connector (no token refresh).
    cfgs = await _build_http_config(row, None)  # type: ignore[arg-type]
    assert cfgs is not None and len(cfgs) == 1
    return dict(cfgs[0].headers)


async def test_should_prefix_bearer_when_secret_header_is_authorization() -> None:
    headers = await _headers(_row("Authorization"))
    assert headers == {"Authorization": "Bearer sk-123"}


async def test_should_send_raw_secret_when_header_is_custom() -> None:
    headers = await _headers(_row("X-API-Key"))
    assert headers == {"X-API-Key": "sk-123"}


async def test_should_treat_authorization_case_insensitively() -> None:
    headers = await _headers(_row("authorization"))
    assert headers == {"authorization": "Bearer sk-123"}


async def test_oauth_token_refresh_goes_through_extension_port() -> None:
    calls: list[dict[str, object]] = []

    class FakeRefreshPort:
        async def ensure_fresh_token(self, *, row, connectors, token_json: str) -> str:
            calls.append({"row": row.id, "connectors": connectors, "token_json": token_json})
            return '{"access_token":"fresh-token"}'

        async def refresh_after_unauthorized(self, *, row, connectors, token_json: str | None):
            raise AssertionError("not used by resolver build")

    old = ext.connector_oauth_refresh
    ext.connector_oauth_refresh = FakeRefreshPort()
    try:
        row = _FakeRow(auth_type="oauth", oauth_token_json='{"access_token":"old-token"}')
        connectors = object()
        cfgs = await _build_http_config(row, connectors)  # type: ignore[arg-type]
    finally:
        ext.connector_oauth_refresh = old

    assert cfgs is not None
    assert dict(cfgs[0].headers) == {"Authorization": "Bearer fresh-token"}
    assert calls == [
        {
            "row": "c1",
            "connectors": connectors,
            "token_json": '{"access_token":"old-token"}',
        }
    ]
