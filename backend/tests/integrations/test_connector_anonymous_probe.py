"""Tests for the "OAuth discoverable ≠ OAuth mandatory" behaviour.

A freemium MCP server (e.g. Firecrawl) advertises
``/.well-known/oauth-protected-resource`` so signed-in users get per-account
attribution, yet still serves fully anonymous calls. The connector flow must NOT
force such a server into an OAuth login the user never asked for: it should stay
``auth_type="none"`` whenever an unauthenticated ``initialize`` succeeds.

Covers:
- ``OAuthDiscoverHelper.server_allows_anonymous`` (the probe itself), and
- ``discover_connector`` wiring (metadata + anonymous-allowed ⇒ reports none).
"""

from __future__ import annotations

import httpx
import pytest

import valuz_agent.integrations.connector_oauth as co
from valuz_agent.api.routes.connectors import (
    DiscoverConnectorRequest,
    discover_connector,
)
from valuz_agent.integrations.connector_oauth import OAuthDiscoverHelper, OauthMetadata

_URL = "https://mcp.firecrawl.dev/v2/mcp"


async def _make_helper(handler) -> OAuthDiscoverHelper:
    h = OAuthDiscoverHelper(_URL)
    await h._client.aclose()
    h._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return h


@pytest.fixture(autouse=True)
def _instant_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """No real sleeps — server_allows_anonymous retries with a backoff."""

    async def _no_sleep(*_a: object, **_k: object) -> None:
        return None

    monkeypatch.setattr(co.asyncio, "sleep", _no_sleep)


# ---------------------------------------------------------------------------
# server_allows_anonymous
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_allows_anonymous_true_on_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert b'"method":"initialize"' in request.content.replace(b" ", b"")
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})

    h = await _make_helper(handler)
    try:
        assert await h.server_allows_anonymous() is True
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_allows_anonymous_true_on_transient_401_then_success() -> None:
    """A throttled 401 must not force OAuth: if a later attempt succeeds, the
    server is anonymous-allowed."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(401, headers={"www-authenticate": "Bearer"})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})

    h = await _make_helper(handler)
    try:
        assert await h.server_allows_anonymous() is True
        assert calls["n"] == 2  # retried past the transient 401
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_allows_anonymous_false_on_401() -> None:
    """A server that rejects *every* attempt is genuinely auth-required."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, headers={"www-authenticate": "Bearer"})

    h = await _make_helper(handler)
    try:
        assert await h.server_allows_anonymous() is False
        assert calls["n"] == 3  # all attempts tried before giving up
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_allows_anonymous_false_on_transport_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    h = await _make_helper(handler)
    try:
        assert await h.server_allows_anonymous() is False
    finally:
        await h.close()


# ---------------------------------------------------------------------------
# discover_connector wiring
# ---------------------------------------------------------------------------

_META = OauthMetadata(
    authorization_endpoint="https://auth.example/authorize",
    token_endpoint="https://auth.example/token",
    registration_endpoint="https://auth.example/register",
)


@pytest.mark.asyncio
async def test_discover_reports_none_when_server_serves_anonymous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_meta(self: OAuthDiscoverHelper) -> OauthMetadata:
        return _META

    async def fake_anon(self: OAuthDiscoverHelper) -> bool:
        return True

    monkeypatch.setattr(co.OAuthDiscoverHelper, "get_oauth_metadata", fake_meta)
    monkeypatch.setattr(co.OAuthDiscoverHelper, "server_allows_anonymous", fake_anon)

    resp = await discover_connector(DiscoverConnectorRequest(url=_URL, transport="http"))

    assert resp.auth_type == "none"
    assert resp.discovered is False
    assert resp.oauth_authorization_endpoint is None


@pytest.mark.asyncio
async def test_discover_reports_oauth_when_anonymous_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_meta(self: OAuthDiscoverHelper) -> OauthMetadata:
        return _META

    async def fake_anon(self: OAuthDiscoverHelper) -> bool:
        return False

    monkeypatch.setattr(co.OAuthDiscoverHelper, "get_oauth_metadata", fake_meta)
    monkeypatch.setattr(co.OAuthDiscoverHelper, "server_allows_anonymous", fake_anon)

    resp = await discover_connector(DiscoverConnectorRequest(url=_URL, transport="http"))

    assert resp.auth_type == "oauth"
    assert resp.discovered is True
    assert resp.oauth_authorization_endpoint == "https://auth.example/authorize"
    assert resp.oauth_token_endpoint == "https://auth.example/token"


# ---------------------------------------------------------------------------
# catalog entry
# ---------------------------------------------------------------------------


def test_firecrawl_catalog_entry_is_anonymous_http() -> None:
    from valuz_agent.api.routes.connectors import CONNECTOR_DIRECTORY

    fc = next((c for c in CONNECTOR_DIRECTORY if c["slug"] == "firecrawl"), None)
    assert fc is not None, "firecrawl missing from connector catalog"
    assert fc["auth_type"] == "none"
    assert fc["transport"] == "http"
    assert fc["url"] == _URL
