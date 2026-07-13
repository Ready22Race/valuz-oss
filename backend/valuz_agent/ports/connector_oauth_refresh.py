"""Connector OAuth refresh extension port.

OSS refreshes expired local OAuth tokens directly against the connector's token
endpoint. Overlays that use a remote desired-state authority can replace this
port so runtime reads wait for the next synced token snapshot instead of
mutating local secret material.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any

_token_refresh_locks: dict[str, asyncio.Lock] = {}


def _token_refresh_lock(connector_id: str) -> asyncio.Lock:
    lock = _token_refresh_locks.get(connector_id)
    if lock is None:
        lock = _token_refresh_locks[connector_id] = asyncio.Lock()
    return lock


class ConnectorOAuthRefreshPort(ABC):
    """Refresh policy for OAuth connector tokens."""

    @abstractmethod
    async def ensure_fresh_token(
        self,
        *,
        row: Any,
        connectors: Any,
        token_json: str,
    ) -> str:
        """Return token JSON suitable for runtime config construction."""
        ...

    @abstractmethod
    async def refresh_after_unauthorized(
        self,
        *,
        row: Any,
        connectors: Any,
        token_json: str | None,
    ) -> str | None:
        """Try a forced refresh after a probe receives 401."""
        ...


class LocalConnectorOAuthRefreshProvider(ConnectorOAuthRefreshPort):
    """OSS default: refresh against the local connector's token endpoint."""

    async def ensure_fresh_token(
        self,
        *,
        row: Any,
        connectors: Any,
        token_json: str,
    ) -> str:
        from valuz_agent.infra.time_utils import now_ms
        from valuz_agent.integrations.connector_oauth import oauth_token_is_expired

        if not oauth_token_is_expired(row, now_ms()):
            return token_json

        async with _token_refresh_lock(row.id):
            fresh = await connectors.get_by_id(row.user_id, row.id)
            target = fresh if fresh is not None else row
            if not oauth_token_is_expired(target, now_ms()):
                return target.oauth_token_json or token_json
            refreshed = await self.refresh_after_unauthorized(
                row=target,
                connectors=connectors,
                token_json=target.oauth_token_json or token_json,
            )
            return refreshed or target.oauth_token_json or token_json

    async def refresh_after_unauthorized(
        self,
        *,
        row: Any,
        connectors: Any,
        token_json: str | None,
    ) -> str | None:
        from valuz_agent.infra.config import settings
        from valuz_agent.infra.time_utils import now_ms
        from valuz_agent.integrations.connector_oauth import try_refresh_connector_token

        _ = token_json
        new_access = await try_refresh_connector_token(
            row,
            redirect_uri=f"{settings.backend_base_url}/v1/connectors/oauth/callback",
            now_ms=now_ms(),
        )
        if new_access is None:
            return None
        await connectors.update(row)
        return row.oauth_token_json


class NoopConnectorOAuthRefreshProvider(ConnectorOAuthRefreshPort):
    """No-op provider for overlays that refresh through external sync."""

    async def ensure_fresh_token(
        self,
        *,
        row: Any,
        connectors: Any,
        token_json: str,
    ) -> str:
        _ = (row, connectors)
        return token_json

    async def refresh_after_unauthorized(
        self,
        *,
        row: Any,
        connectors: Any,
        token_json: str | None,
    ) -> str | None:
        _ = (row, connectors, token_json)
        return None


def get_connector_oauth_refresh_port() -> ConnectorOAuthRefreshPort:
    from valuz_agent.ports.extensions import ext

    return ext.connector_oauth_refresh


def set_connector_oauth_refresh_port(port: ConnectorOAuthRefreshPort) -> None:
    from valuz_agent.ports.extensions import ext

    ext.connector_oauth_refresh = port


__all__ = [
    "ConnectorOAuthRefreshPort",
    "LocalConnectorOAuthRefreshProvider",
    "NoopConnectorOAuthRefreshProvider",
    "get_connector_oauth_refresh_port",
    "set_connector_oauth_refresh_port",
]
