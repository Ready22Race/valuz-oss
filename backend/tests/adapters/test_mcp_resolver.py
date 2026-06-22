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


@dataclass
class _FakeRow:
    id: str = "c1"
    slug: str = "acme"
    url: str = "https://mcp.acme.test/mcp"
    transport: str = "http"
    auth_type: str = "bearer"
    headers_json: str | None = None
    params_json: str | None = None
    args_json: str | None = None


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
