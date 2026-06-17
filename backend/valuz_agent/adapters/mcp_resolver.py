"""Resolve a list of enabled MCP-provider slugs into kernel MCP wire schemas.

The capability resolver receives the slugs the caller chose for a session and
delegates to this module to materialise them. Each provider knows how to
acquire its credentials (OAuth account secret store, future API-key vaults,
etc.) and how to build its URL.

The resulting wire-schema list is handed to the kernel verbatim. The
kernel runtime registers them under their ``name`` so the agent's tool calls
land in the right server.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from app.schemas import (
    McpHttpServerConfigSchema as McpHttpServerConfig,
)
from app.schemas import (
    McpServerConfigSchema as McpServerConfig,
)
from app.schemas import (
    McpStdioServerConfigSchema as McpStdioServerConfig,
)

# Side-effect import — surfaces ``src.core...`` on sys.path.
import valuz_agent.boot.kernel  # noqa: F401
from valuz_agent.infra.secret_store import FileSecretStore
from valuz_agent.modules.connectors.datastore import ConnectorDatastore
from valuz_agent.modules.connectors.service import build_overrides, merge_params_into_url

logger = logging.getLogger(__name__)

# Single-flight guard so concurrent session builds for the same connector don't
# fire overlapping refreshes (rotating refresh tokens are single-use). In-process
# only — adequate for the desktop backend; a multi-instance deployment would race
# at most one redundant refresh.
_token_refresh_locks: dict[str, asyncio.Lock] = {}


def _token_refresh_lock(connector_id: str) -> asyncio.Lock:
    lock = _token_refresh_locks.get(connector_id)
    if lock is None:
        lock = _token_refresh_locks[connector_id] = asyncio.Lock()
    return lock


async def _ensure_fresh_oauth_token(row: Any, secrets: FileSecretStore, token_json: str) -> str:
    """Proactively refresh an OAuth connector's token if the expiry sidecar shows
    it has lapsed.

    The resolver builds the server config ahead of time, so it can't react to a
    runtime 401 — instead it refreshes *before* handing the token to the kernel.
    Returns the (possibly refreshed) token JSON; on any failure it returns the
    original blob so the caller still attempts the old token (and the runtime's
    own 401 surfaces normally).
    """
    from valuz_agent.infra.config import settings as _settings
    from valuz_agent.infra.time_utils import now_ms
    from valuz_agent.integrations.connector_oauth import (
        oauth_token_is_expired,
        oauth_token_ref,
        try_refresh_connector_token,
    )

    if not oauth_token_is_expired(row.id, secrets, now_ms()):
        return token_json
    async with _token_refresh_lock(row.id):
        # Re-check under the lock: a sibling build may have refreshed already.
        if not oauth_token_is_expired(row.id, secrets, now_ms()):
            return secrets.get(oauth_token_ref(row.id)) or token_json
        await try_refresh_connector_token(
            connector_id=row.id,
            server_url=row.url or "",
            oauth_metadata_json=row.oauth_metadata_json,
            oauth_client_info_json=row.oauth_client_info_json,
            redirect_uri=f"{_settings.backend_base_url}/v1/connectors/oauth/callback",
            secrets=secrets,
            now_ms=now_ms(),
        )
        return secrets.get(oauth_token_ref(row.id)) or token_json


async def resolve_mcp_servers(
    *,
    secrets: FileSecretStore,
    enabled_slugs: list[str],
    connectors: ConnectorDatastore | None = None,
) -> list[McpServerConfig]:
    """Translate enabled MCP-provider slugs into kernel ``McpServerConfig`` rows."""
    out: list[McpServerConfig] = []
    seen_names: set[str] = set()

    for slug in enabled_slugs:
        cfgs = await _resolve_connector_slug(slug, connectors, secrets)
        if cfgs is None:
            logger.info("mcp resolver: slug %s unknown or has no credentials — skipping", slug)
            continue
        for cfg in cfgs:
            if cfg.name in seen_names or cfg.name == "harness":
                continue
            seen_names.add(cfg.name)
            out.append(cfg)

    return out


async def _resolve_connector_slug(
    slug: str,
    connectors: ConnectorDatastore | None,
    secrets: FileSecretStore,
) -> list[McpServerConfig] | None:
    if connectors is None:
        return None
    from valuz_agent.infra.auth_context import require_current_user_id

    row = await connectors.get_by_slug(require_current_user_id(), slug)
    if row is None or not row.enabled:
        return None

    if row.transport == "stdio":
        return _build_stdio_config(row, secrets)

    return await _build_http_config(row, secrets)


async def _build_http_config(row, secrets: FileSecretStore) -> list[McpServerConfig] | None:
    # Single injection truth shared with the probe (Acceptance #8 — probe
    # and runtime must produce byte-identical headers/params).
    headers, params = build_overrides(row, secrets)

    if row.auth_type == "oauth":
        # OAuth layers on AFTER build_overrides — it needs a live token.
        token_json = secrets.get(f"connector/{row.id}/oauth_token")
        if not token_json:
            logger.info("mcp resolver: connector %s oauth token not found", row.slug)
            return None
        # Self-heal an expired token before the runtime ever makes a call.
        token_json = await _ensure_fresh_oauth_token(row, secrets, token_json)
        try:
            token_data = json.loads(token_json)
            access_token = token_data.get("access_token", "")
        except (json.JSONDecodeError, AttributeError):
            return None
        if not access_token:
            return None
        headers["Authorization"] = f"Bearer {access_token}"

    url = row.url or ""
    transport = row.transport if row.transport in ("http", "sse") else "http"

    if not url:
        return None

    if "{module}" in url:
        modules: list[str] = []
        if row.args_json:
            try:
                parsed = json.loads(row.args_json)
                if isinstance(parsed, list):
                    modules = [str(m) for m in parsed]
            except json.JSONDecodeError:
                pass
        if not modules:
            return []
        return [
            McpHttpServerConfig(
                name=f"{row.slug}_{module}",
                url=merge_params_into_url(url.replace("{module}", module), params),
                transport=transport,  # type: ignore[arg-type]
                headers=dict(headers),
            )
            for module in modules
        ]

    return [
        McpHttpServerConfig(
            name=row.slug,
            url=merge_params_into_url(url, params),
            transport=transport,  # type: ignore[arg-type]
            headers=dict(headers),
        )
    ]


def _bundled_mcp_servers_dir() -> str:
    """Absolute path to the bundled MCP server tree
    (``valuz_agent/resources/mcp_servers``), as a POSIX string.

    Bundled stdio connectors reference their entry point with the
    ``{mcp_dir}`` placeholder so the catalog stays path-agnostic and the
    same JSON works in a dev checkout and a PyInstaller-frozen app (where
    ``resources/`` lands under ``_internal/valuz_agent/``).
    """
    from pathlib import Path

    return (Path(__file__).resolve().parent.parent / "resources" / "mcp_servers").as_posix()


def expand_mcp_dir(value: str) -> str:
    """Substitute the ``{mcp_dir}`` placeholder a bundled stdio connector uses
    for its entry point with the absolute bundled-server tree path.

    The single source of truth for this expansion — used both by the runtime
    resolver (``_build_stdio_config``) and the connector test probe, so a
    bundled connector that runs at session time also passes the UI's
    "test connection" probe.
    """
    return value.replace("{mcp_dir}", _bundled_mcp_servers_dir())


def _build_stdio_config(row, secrets: FileSecretStore) -> list[McpServerConfig] | None:
    import shlex

    if not row.command:
        logger.info("mcp resolver: stdio connector %s has no command", row.slug)
        return None

    def _expand(value: str) -> str:
        return expand_mcp_dir(value)

    raw_command = _expand(row.command)
    extra_args: tuple[str, ...] = ()
    if " " in raw_command:
        parts = shlex.split(raw_command)
        raw_command = parts[0]
        extra_args = tuple(parts[1:])

    args: tuple[str, ...] = extra_args
    if row.args_json:
        try:
            parsed = json.loads(row.args_json)
            if isinstance(parsed, list):
                args = extra_args + tuple(_expand(str(a)) for a in parsed)
        except json.JSONDecodeError:
            pass
    env: dict[str, str] = {}
    if row.env_json:
        try:
            parsed_env = json.loads(row.env_json)
            if isinstance(parsed_env, dict):
                env = {str(k): str(v) for k, v in parsed_env.items()}
        except json.JSONDecodeError:
            pass
    # Inject secret credentials (e.g. WIND_API_KEY) declared in the
    # connector's cred manifest with ``target == "env"``. Values come from
    # the secret store, never persisted in the connector row.
    for m in _parse_manifest(row.cred_manifest_json):
        if m.get("target") != "env":
            continue
        val = secrets.get(m["secret_ref"])
        if val is not None:
            env[m["name"]] = val
    return [
        McpStdioServerConfig(
            name=row.slug,
            command=raw_command,
            args=args,
            env=env,
        )
    ]


def _parse_manifest(raw: str | None) -> list[dict[str, Any]]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return [m for m in parsed if isinstance(m, dict)] if isinstance(parsed, list) else []


__all__ = ["resolve_mcp_servers"]
