"""Unit tests for the OAuth connector token-lifecycle helpers
(``persist_oauth_token`` / ``oauth_token_is_expired`` / ``try_refresh_connector_token``).

These cover the silent-refresh path the connector probe + runtime resolver rely
on, without a live OAuth server: the refresh HTTP call is monkeypatched. The
token + expiry now live on the connector row's columns, so the helpers mutate a
row in place (the caller commits it).
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from mcp.shared.auth import OAuthToken

from valuz_agent.integrations import connector_oauth as co
from valuz_agent.integrations.connector_oauth import (
    OauthMetadata,
    oauth_token_is_expired,
    persist_oauth_token,
    try_refresh_connector_token,
)

_META = OauthMetadata(
    authorization_endpoint="https://auth.example/authorize",
    token_endpoint="https://auth.example/token",
).model_dump_json()


@dataclass
class _FakeRow:
    """The slice of ``ConnectorRow`` the lifecycle helpers touch."""

    id: str = "c1"
    url: str = "https://mcp.example/mcp"
    oauth_metadata_json: str | None = _META
    oauth_client_info_json: str | None = None
    oauth_token_json: str | None = None
    oauth_token_expires_at: int | None = None


def test_persist_writes_token_and_expiry() -> None:
    row = _FakeRow()
    persist_oauth_token(
        row, OAuthToken(access_token="a1", refresh_token="r1", expires_in=3600), 1_000
    )

    assert "a1" in (row.oauth_token_json or "")
    assert row.oauth_token_expires_at == 1_000 + 3_600_000


def test_persist_clears_expiry_when_no_expires_in() -> None:
    row = _FakeRow(oauth_token_expires_at=999)
    persist_oauth_token(row, OAuthToken(access_token="a1"), 1_000)
    assert row.oauth_token_expires_at is None


def test_is_expired_unknown_without_expiry() -> None:
    # No stored expiry → never assume expiry (refresh happens reactively on 401).
    assert oauth_token_is_expired(_FakeRow(), now_ms=10**12) is False


def test_is_expired_respects_skew() -> None:
    row = _FakeRow(oauth_token_expires_at=100_000)
    assert oauth_token_is_expired(row, now_ms=10_000, skew_ms=0) is False
    # within the 60s skew window of expiry
    assert oauth_token_is_expired(row, now_ms=50_000, skew_ms=60_000) is True


@pytest.mark.asyncio
async def test_refresh_success_rotates_and_persists(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _FakeRow(oauth_client_info_json='{"client_id": "cid"}')
    persist_oauth_token(
        row, OAuthToken(access_token="old", refresh_token="r-old", expires_in=10), 0
    )

    async def fake_refresh(self: object, refresh_token: str) -> OAuthToken:
        assert refresh_token == "r-old"
        return OAuthToken(access_token="new", refresh_token="r-new", expires_in=7200)

    monkeypatch.setattr(co.McpOauthHelper, "refresh_access_token", fake_refresh)

    new_access = await try_refresh_connector_token(
        row, redirect_uri="https://host/v1/connectors/oauth/callback", now_ms=1_000
    )

    assert new_access == "new"
    stored = OAuthToken.model_validate_json(row.oauth_token_json or "{}")
    assert stored.access_token == "new"
    assert stored.refresh_token == "r-new"
    assert row.oauth_token_expires_at == 1_000 + 7_200_000


@pytest.mark.asyncio
async def test_refresh_keeps_old_refresh_token_when_server_omits_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _FakeRow()
    persist_oauth_token(row, OAuthToken(access_token="old", refresh_token="r-old"), 0)

    async def fake_refresh(self: object, refresh_token: str) -> OAuthToken:
        return OAuthToken(access_token="new")  # non-rotating server: no refresh_token

    monkeypatch.setattr(co.McpOauthHelper, "refresh_access_token", fake_refresh)

    await try_refresh_connector_token(row, redirect_uri="https://host/cb", now_ms=0)

    stored = OAuthToken.model_validate_json(row.oauth_token_json or "{}")
    assert stored.refresh_token == "r-old"


@pytest.mark.asyncio
async def test_refresh_returns_none_without_refresh_token() -> None:
    row = _FakeRow()
    persist_oauth_token(row, OAuthToken(access_token="old"), 0)
    out = await try_refresh_connector_token(row, redirect_uri="https://host/cb", now_ms=0)
    assert out is None


@pytest.mark.asyncio
async def test_refresh_returns_none_when_server_rejects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _FakeRow()
    persist_oauth_token(row, OAuthToken(access_token="old", refresh_token="r-old"), 0)

    async def boom(self: object, refresh_token: str) -> OAuthToken:
        raise ValueError("invalid_grant")

    monkeypatch.setattr(co.McpOauthHelper, "refresh_access_token", boom)

    out = await try_refresh_connector_token(row, redirect_uri="https://host/cb", now_ms=0)
    assert out is None
    # original token is left intact for the caller to fall back on
    stored = OAuthToken.model_validate_json(row.oauth_token_json or "{}")
    assert stored.access_token == "old"
