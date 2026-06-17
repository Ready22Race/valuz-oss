"""Unit tests for the OAuth connector token-lifecycle helpers
(``persist_oauth_token`` / ``oauth_token_is_expired`` / ``try_refresh_connector_token``).

These cover the silent-refresh path the connector probe + runtime resolver rely
on, without a live OAuth server: the refresh HTTP call is monkeypatched.
"""

from __future__ import annotations

import pytest
from mcp.shared.auth import OAuthToken

from valuz_agent.integrations import connector_oauth as co
from valuz_agent.integrations.connector_oauth import (
    OauthMetadata,
    oauth_token_expiry_ref,
    oauth_token_is_expired,
    oauth_token_ref,
    persist_oauth_token,
    try_refresh_connector_token,
)


class FakeSecretStore:
    """Minimal in-memory ``SecretStore``."""

    def __init__(self) -> None:
        self._d: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self._d.get(key)

    def put(self, key: str, value: str) -> None:
        self._d[key] = value

    def delete(self, key: str) -> None:
        self._d.pop(key, None)


_META = OauthMetadata(
    authorization_endpoint="https://auth.example/authorize",
    token_endpoint="https://auth.example/token",
).model_dump_json()


def test_persist_writes_token_and_expiry_sidecar() -> None:
    s = FakeSecretStore()
    token = OAuthToken(access_token="a1", refresh_token="r1", expires_in=3600)

    persist_oauth_token("c1", token, s, now_ms=1_000)

    assert "a1" in (s.get(oauth_token_ref("c1")) or "")
    # 1_000 + 3600 * 1000
    assert s.get(oauth_token_expiry_ref("c1")) == str(1_000 + 3_600_000)


def test_persist_clears_sidecar_when_no_expires_in() -> None:
    s = FakeSecretStore()
    s.put(oauth_token_expiry_ref("c1"), "999")
    persist_oauth_token("c1", OAuthToken(access_token="a1"), s, now_ms=1_000)
    assert s.get(oauth_token_expiry_ref("c1")) is None


def test_is_expired_unknown_without_sidecar() -> None:
    s = FakeSecretStore()
    # No sidecar → never assume expiry (refresh happens reactively on 401).
    assert oauth_token_is_expired("c1", s, now_ms=10**12) is False


def test_is_expired_respects_skew() -> None:
    s = FakeSecretStore()
    s.put(oauth_token_expiry_ref("c1"), str(100_000))
    assert oauth_token_is_expired("c1", s, now_ms=10_000, skew_ms=0) is False
    # within the 60s skew window of expiry
    assert oauth_token_is_expired("c1", s, now_ms=50_000, skew_ms=60_000) is True


@pytest.mark.asyncio
async def test_refresh_success_rotates_and_persists(monkeypatch: pytest.MonkeyPatch) -> None:
    s = FakeSecretStore()
    persist_oauth_token(
        "c1",
        OAuthToken(access_token="old", refresh_token="r-old", expires_in=10),
        s,
        now_ms=0,
    )

    async def fake_refresh(self: object, refresh_token: str) -> OAuthToken:
        assert refresh_token == "r-old"
        return OAuthToken(access_token="new", refresh_token="r-new", expires_in=7200)

    monkeypatch.setattr(co.McpOauthHelper, "refresh_access_token", fake_refresh)

    new_access = await try_refresh_connector_token(
        connector_id="c1",
        server_url="https://mcp.example/mcp",
        oauth_metadata_json=_META,
        oauth_client_info_json='{"client_id": "cid"}',
        redirect_uri="https://host/v1/connectors/oauth/callback",
        secrets=s,
        now_ms=1_000,
    )

    assert new_access == "new"
    stored = OAuthToken.model_validate_json(s.get(oauth_token_ref("c1")) or "{}")
    assert stored.access_token == "new"
    assert stored.refresh_token == "r-new"
    assert s.get(oauth_token_expiry_ref("c1")) == str(1_000 + 7_200_000)


@pytest.mark.asyncio
async def test_refresh_keeps_old_refresh_token_when_server_omits_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    s = FakeSecretStore()
    persist_oauth_token("c1", OAuthToken(access_token="old", refresh_token="r-old"), s, now_ms=0)

    async def fake_refresh(self: object, refresh_token: str) -> OAuthToken:
        return OAuthToken(access_token="new")  # non-rotating server: no refresh_token

    monkeypatch.setattr(co.McpOauthHelper, "refresh_access_token", fake_refresh)

    await try_refresh_connector_token(
        connector_id="c1",
        server_url="https://mcp.example/mcp",
        oauth_metadata_json=_META,
        oauth_client_info_json=None,
        redirect_uri="https://host/v1/connectors/oauth/callback",
        secrets=s,
        now_ms=0,
    )

    stored = OAuthToken.model_validate_json(s.get(oauth_token_ref("c1")) or "{}")
    assert stored.refresh_token == "r-old"


@pytest.mark.asyncio
async def test_refresh_returns_none_without_refresh_token() -> None:
    s = FakeSecretStore()
    persist_oauth_token("c1", OAuthToken(access_token="old"), s, now_ms=0)
    out = await try_refresh_connector_token(
        connector_id="c1",
        server_url="https://mcp.example/mcp",
        oauth_metadata_json=_META,
        oauth_client_info_json=None,
        redirect_uri="https://host/cb",
        secrets=s,
        now_ms=0,
    )
    assert out is None


@pytest.mark.asyncio
async def test_refresh_returns_none_when_server_rejects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    s = FakeSecretStore()
    persist_oauth_token("c1", OAuthToken(access_token="old", refresh_token="r-old"), s, now_ms=0)

    async def boom(self: object, refresh_token: str) -> OAuthToken:
        raise ValueError("invalid_grant")

    monkeypatch.setattr(co.McpOauthHelper, "refresh_access_token", boom)

    out = await try_refresh_connector_token(
        connector_id="c1",
        server_url="https://mcp.example/mcp",
        oauth_metadata_json=_META,
        oauth_client_info_json=None,
        redirect_uri="https://host/cb",
        secrets=s,
        now_ms=0,
    )
    assert out is None
    # original token is left intact for the caller to fall back on
    stored = OAuthToken.model_validate_json(s.get(oauth_token_ref("c1")) or "{}")
    assert stored.access_token == "old"
