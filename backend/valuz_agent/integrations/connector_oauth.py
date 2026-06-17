"""MCP connector OAuth helpers — ported from mcp-proxy/app/modules/mcp/helper.py.

Implements the MCP RFC 7591 dynamic client registration + PKCE authorization
code flow. Works for any MCP server that advertises a
``/.well-known/oauth-protected-resource`` (or ``/.well-known/oauth-authorization-server``)
discovery document.

Usage
-----
1. ``OAuthDiscoverHelper(server_url).get_oauth_metadata()``
   → ``OauthMetadata`` (authorization_endpoint, token_endpoint, …)

2. ``McpOauthHelper(...).register_client(redirect_uris)``
   → ``OAuthClientInformationFull`` (client_id, client_secret)

3. ``McpOauthHelper(...).get_authorization_url()``
   → ``(authorization_url, state, code_verifier)``

4. ``McpOauthHelper(...).get_oauth_token(code, code_verifier)``
   → ``OAuthToken``

PKCE state is stored in ``FileSecretStore`` under
``connector/oauth_state/{state}`` so the callback can look it up without
requiring sticky sessions or an external cache. The entry is written at (3)
and consumed at (4).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Protocol
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import httpx
from mcp.client.auth import PKCEParameters
from mcp.client.streamable_http import MCP_PROTOCOL_VERSION
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthToken,
    ProtectedResourceMetadata,
)
from mcp.shared.auth import OAuthMetadata as _OAuthMetadata
from mcp.shared.auth_utils import check_resource_allowed, resource_url_from_server_url
from mcp.types import LATEST_PROTOCOL_VERSION
from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class McpOAuthMetadata(_OAuthMetadata):
    issuer: str


class OauthMetadata(BaseModel):
    resource: str | None = None
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str | None = None
    response_types_supported: list[str] = Field(default_factory=lambda: ["code"])
    bearer_methods_supported: list[str] = Field(default_factory=lambda: ["header"])
    grant_types_supported: list[str] = ["authorization_code", "refresh_token"]
    code_challenge_methods_supported: list[str] = ["plain", "S256"]
    token_endpoint_auth_methods_supported: list[str] | None = None


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


def _url_with_params(url: str, params: dict) -> str:
    """Merge ``params`` into ``url``'s query string."""
    parts = list(urlparse(url))
    existing = {k: v[0] if len(v) == 1 else v for k, v in parse_qs(parts[4]).items()}
    existing.update(params)
    parts[4] = urlencode(existing, doseq=True)
    return urlunparse(parts)


def _base_url(server_url: str) -> str:
    parsed = urlparse(server_url)
    return f"{parsed.scheme}://{parsed.netloc}"


# ---------------------------------------------------------------------------
# HTTP helpers — simple sync wrappers (no tenacity retry to keep deps lean)
# ---------------------------------------------------------------------------


async def _send(client: httpx.AsyncClient, request: httpx.Request) -> httpx.Response:
    return await client.send(request)


# ---------------------------------------------------------------------------
# OAuthDiscoverHelper
# ---------------------------------------------------------------------------


class OAuthDiscoverHelper:
    """Discover an MCP server's OAuth metadata via RFC 8414 / OIDC well-known endpoints."""

    def __init__(self, server_url: str) -> None:
        self._server_url = server_url
        self._client = httpx.AsyncClient(timeout=30)

    async def close(self) -> None:
        await self._client.aclose()

    async def get_oauth_metadata(self) -> OauthMetadata | None:
        auth_server_url: str | None = None
        protected_resource = await self._discover_protected_resource()
        if protected_resource and protected_resource.authorization_servers:
            auth_server_url = str(protected_resource.authorization_servers[0])

        origin_meta = await self._discover_oauth_metadata(auth_server_url)
        if origin_meta is None:
            return None

        values: dict = {
            "authorization_endpoint": str(origin_meta.authorization_endpoint),
            "token_endpoint": str(origin_meta.token_endpoint),
            "response_types_supported": origin_meta.response_types_supported,
            "grant_types_supported": origin_meta.grant_types_supported,
            "token_endpoint_auth_methods_supported": (
                origin_meta.token_endpoint_auth_methods_supported
            ),
        }
        if origin_meta.registration_endpoint:
            values["registration_endpoint"] = str(origin_meta.registration_endpoint)
        if protected_resource:
            if protected_resource.resource:
                values["resource"] = str(protected_resource.resource)
            if protected_resource.bearer_methods_supported:
                values["bearer_methods_supported"] = protected_resource.bearer_methods_supported

        return OauthMetadata.model_validate(values)

    def _get_discovery_urls(self, auth_server_url: str | None = None) -> list[str]:
        target = auth_server_url or self._server_url
        parsed = urlparse(target)
        base = f"{parsed.scheme}://{parsed.netloc}"
        urls: list[str] = []

        if parsed.path and parsed.path != "/":
            urls.append(
                urljoin(base, f"/.well-known/oauth-authorization-server{parsed.path.rstrip('/')}")
            )

        urls.append(urljoin(base, "/.well-known/oauth-authorization-server"))

        if parsed.path and parsed.path != "/":
            urls.append(
                urljoin(base, f"/.well-known/openid-configuration{parsed.path.rstrip('/')}")
            )

        urls.append(f"{target.rstrip('/')}/.well-known/openid-configuration")
        return urls

    def _get_protected_resource_urls(self) -> list[str]:
        """RFC 9728: try path-aware URL first, then base URL."""
        parsed = urlparse(self._server_url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        path = parsed.path.rstrip("/")
        urls: list[str] = []
        if path and path != "/":
            urls.append(urljoin(base, f"/.well-known/oauth-protected-resource{path}"))
        urls.append(urljoin(base, "/.well-known/oauth-protected-resource"))
        return urls

    async def _discover_protected_resource(self) -> ProtectedResourceMetadata | None:
        # Step 1: probe the MCP endpoint; extract resource_metadata from 401 www-authenticate.
        resource_metadata_url = await self._probe_for_resource_metadata()
        if resource_metadata_url:
            result = await self._fetch_protected_resource(resource_metadata_url)
            if result:
                return result

        # Fallback: construct well-known URLs from the server path.
        for url in self._get_protected_resource_urls():
            result = await self._fetch_protected_resource(url)
            if result:
                return result
        return None

    async def _probe_for_resource_metadata(self) -> str | None:
        """GET the MCP endpoint and extract resource_metadata from a 401 www-authenticate header."""
        try:
            req = httpx.Request(
                "GET",
                self._server_url,
                headers={MCP_PROTOCOL_VERSION: LATEST_PROTOCOL_VERSION},
            )
            resp = await _send(self._client, req)
            if resp.status_code == 401:
                www_auth = resp.headers.get("www-authenticate", "")
                m = re.search(r'resource_metadata="([^"]+)"', www_auth)
                if m:
                    return m.group(1)
        except httpx.HTTPError as exc:
            logger.debug("MCP endpoint probe failed for %s: %s", self._server_url, exc)
        return None

    async def _fetch_protected_resource(self, url: str) -> ProtectedResourceMetadata | None:
        req = httpx.Request("GET", url, headers={MCP_PROTOCOL_VERSION: LATEST_PROTOCOL_VERSION})
        try:
            resp = await _send(self._client, req)
        except httpx.HTTPError as exc:
            logger.debug("Protected resource fetch failed for %s: %s", url, exc)
            return None
        if resp.status_code != 200:
            return None
        try:
            content = await resp.aread()
            return ProtectedResourceMetadata.model_validate_json(content)
        except (ValidationError, Exception):
            return None

    async def _discover_oauth_metadata(
        self, auth_server_url: str | None = None
    ) -> McpOAuthMetadata | None:
        for url in self._get_discovery_urls(auth_server_url):
            req = httpx.Request("GET", url, headers={MCP_PROTOCOL_VERSION: LATEST_PROTOCOL_VERSION})
            try:
                resp = await _send(self._client, req)
            except httpx.HTTPError:
                continue
            if resp.status_code == 200:
                try:
                    content = await resp.aread()
                    return McpOAuthMetadata.model_validate_json(content)
                except (ValidationError, Exception):
                    continue
            elif resp.status_code >= 500:
                break
        return None


# ---------------------------------------------------------------------------
# McpOauthHelper
# ---------------------------------------------------------------------------


class McpOauthHelper:
    """Handles client registration, authorization URL generation, and token exchange."""

    def __init__(
        self,
        *,
        server_url: str,
        client_metadata: OAuthClientMetadata,
        token_endpoint: str,
        authorization_endpoint: str,
        resource: str | None = None,
        registration_endpoint: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
    ) -> None:
        self._server_url = server_url
        self.client_metadata = client_metadata
        self.token_endpoint = token_endpoint
        self.authorization_endpoint = authorization_endpoint
        self.resource = resource
        self.registration_endpoint = registration_endpoint
        self.client_id = client_id
        self.client_secret = client_secret
        self._client = httpx.AsyncClient(timeout=30)

    async def close(self) -> None:
        await self._client.aclose()

    async def register_client(self, redirect_uris: list[str]) -> OAuthClientInformationFull:
        """Dynamic client registration (RFC 7591).

        Falls back to ``/register`` on the server base URL when
        ``registration_endpoint`` is absent.
        """
        reg_url = self.registration_endpoint or urljoin(_base_url(self._server_url), "/register")
        body = self.client_metadata.model_dump(by_alias=True, mode="json", exclude_none=True)
        body["redirect_uris"] = redirect_uris

        req = httpx.Request(
            "POST",
            reg_url,
            json=body,
            headers={"Content-Type": "application/json"},
        )
        resp = await _send(self._client, req)
        if resp.status_code not in (200, 201):
            await resp.aread()
            raise ValueError(f"Client registration failed: {resp.status_code} {resp.text}")

        content = await resp.aread()
        try:
            return OAuthClientInformationFull.model_validate_json(content)
        except ValidationError as exc:
            raise ValueError(f"Invalid registration response: {exc}") from exc

    async def get_authorization_url(self) -> tuple[str, str, str]:
        """Return ``(authorization_url, state, code_verifier)``."""
        import secrets as _secrets

        pkce = PKCEParameters.generate()
        state = _secrets.token_urlsafe(32)

        params: dict[str, str] = {
            "response_type": "code",
            "client_id": self.client_id or "",
            "redirect_uri": str(self.client_metadata.redirect_uris[0]),
            "state": state,
            "code_challenge": pkce.code_challenge,
            "code_challenge_method": "S256",
            "resource": self._get_resource_url(),
        }
        if self.client_metadata.scope:
            params["scope"] = self.client_metadata.scope

        url = _url_with_params(self.authorization_endpoint, params)
        return url, state, pkce.code_verifier

    async def get_oauth_token(self, code: str, code_verifier: str) -> OAuthToken:
        token_data: dict[str, str] = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": str(self.client_metadata.redirect_uris[0]),
            "code_verifier": code_verifier,
            "resource": self._get_resource_url(),
        }
        if self.client_id:
            token_data["client_id"] = self.client_id
        if self.client_secret:
            token_data["client_secret"] = self.client_secret

        req = httpx.Request(
            "POST",
            self.token_endpoint,
            data=token_data,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
        )
        resp = await _send(self._client, req)
        return await self._parse_token_response(resp)

    async def refresh_access_token(self, refresh_token: str) -> OAuthToken:
        refresh_data: dict[str, str] = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "resource": self._get_resource_url(),
        }
        if self.client_id:
            refresh_data["client_id"] = self.client_id
        if self.client_secret:
            refresh_data["client_secret"] = self.client_secret

        req = httpx.Request(
            "POST",
            self.token_endpoint,
            data=refresh_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp = await _send(self._client, req)
        return await self._parse_token_response(resp)

    async def _parse_token_response(self, response: httpx.Response) -> OAuthToken:
        if response.status_code != 200:
            try:
                body = response.json()
                detail = body.get("error_description") or body.get("error") or response.text
            except Exception:
                detail = response.text
            raise ValueError(f"Token exchange failed: {detail}")

        content = await response.aread()
        try:
            return OAuthToken.model_validate_json(content)
        except ValidationError as exc:
            raise ValueError(f"Invalid token response: {exc}") from exc

    def _get_resource_url(self) -> str:
        resource = resource_url_from_server_url(self._server_url)
        if self.resource:
            prm = str(self.resource)
            if check_resource_allowed(requested_resource=resource, configured_resource=prm):
                resource = prm
        return resource


# ---------------------------------------------------------------------------
# Token lifecycle helpers (refresh + expiry tracking)
# ---------------------------------------------------------------------------
#
# The access token is persisted as ``OAuthToken`` JSON under
# ``connector/{id}/oauth_token``. ``OAuthToken`` carries only a *relative*
# ``expires_in`` (no issue time), so the absolute expiry is recorded separately
# in a sidecar key ``connector/{id}/oauth_token_expires_at`` (epoch ms). Readers
# that want to refresh proactively consult the sidecar; the token blob keeps its
# original shape so existing readers stay byte-compatible.


class SecretStore(Protocol):
    """The slice of ``FileSecretStore`` these helpers touch (kept narrow so the
    helpers stay unit-testable with a plain dict-backed fake)."""

    def get(self, key: str) -> str | None: ...
    def put(self, key: str, value: str) -> None: ...
    def delete(self, key: str) -> None: ...


def oauth_token_ref(connector_id: str) -> str:
    return f"connector/{connector_id}/oauth_token"


def oauth_token_expiry_ref(connector_id: str) -> str:
    return f"connector/{connector_id}/oauth_token_expires_at"


def persist_oauth_token(
    connector_id: str, token: OAuthToken, secrets: SecretStore, now_ms: int
) -> None:
    """Write the token blob and refresh the absolute-expiry sidecar."""
    secrets.put(oauth_token_ref(connector_id), token.model_dump_json())
    if token.expires_in:
        secrets.put(
            oauth_token_expiry_ref(connector_id),
            str(now_ms + int(token.expires_in) * 1000),
        )
    else:
        try:
            secrets.delete(oauth_token_expiry_ref(connector_id))
        except Exception:
            pass


def oauth_token_is_expired(
    connector_id: str, secrets: SecretStore, now_ms: int, *, skew_ms: int = 60_000
) -> bool:
    """True only when the sidecar proves the token is at/near expiry.

    Unknown expiry (no sidecar — tokens stored before expiry tracking, or
    servers that omit ``expires_in``) returns False: callers must not refresh on
    a guess, they refresh reactively on a real 401 instead.
    """
    raw = secrets.get(oauth_token_expiry_ref(connector_id))
    if not raw:
        return False
    try:
        expires_at = int(raw)
    except (TypeError, ValueError):
        return False
    return now_ms + skew_ms >= expires_at


async def try_refresh_connector_token(
    *,
    connector_id: str,
    server_url: str,
    oauth_metadata_json: str | None,
    oauth_client_info_json: str | None,
    redirect_uri: str,
    secrets: SecretStore,
    now_ms: int,
) -> str | None:
    """Refresh an OAuth connector's access token using its stored refresh token.

    Returns the new access token (and persists the new token + expiry sidecar)
    on success, or None when refresh is impossible (no stored token / no refresh
    token / missing metadata) or the refresh request fails. Never raises — the
    caller falls back to full re-authorization.
    """
    token_json = secrets.get(oauth_token_ref(connector_id))
    if not token_json:
        return None
    try:
        current = OAuthToken.model_validate_json(token_json)
    except ValidationError:
        return None
    if not current.refresh_token or not oauth_metadata_json:
        return None
    try:
        meta = OauthMetadata.model_validate_json(oauth_metadata_json)
    except ValidationError:
        return None

    client_id: str | None = None
    client_secret: str | None = None
    if oauth_client_info_json:
        try:
            info = json.loads(oauth_client_info_json)
            client_id = info.get("client_id")
            client_secret = info.get("client_secret")
        except (ValueError, AttributeError):
            pass

    client_meta = OAuthClientMetadata(
        client_name="Valuz",
        redirect_uris=[redirect_uri],  # type: ignore[list-item]
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",
    )
    helper = McpOauthHelper(
        server_url=server_url,
        client_metadata=client_meta,
        token_endpoint=meta.token_endpoint,
        authorization_endpoint=meta.authorization_endpoint,
        resource=meta.resource,
        client_id=client_id,
        client_secret=client_secret,
    )
    try:
        new_token = await helper.refresh_access_token(current.refresh_token)
    except Exception as exc:  # noqa: BLE001 — refresh is best-effort
        logger.info("connector %s oauth token refresh failed: %s", connector_id, exc)
        return None
    finally:
        await helper.close()

    # Non-rotating servers omit refresh_token on the refresh response — keep the
    # existing one so the next refresh still works.
    if not new_token.refresh_token:
        new_token.refresh_token = current.refresh_token

    persist_oauth_token(connector_id, new_token, secrets, now_ms)
    logger.info("connector %s oauth access token refreshed", connector_id)
    return new_token.access_token


__all__ = [
    "OAuthDiscoverHelper",
    "McpOauthHelper",
    "OauthMetadata",
    "SecretStore",
    "oauth_token_ref",
    "oauth_token_expiry_ref",
    "persist_oauth_token",
    "oauth_token_is_expired",
    "try_refresh_connector_token",
]
