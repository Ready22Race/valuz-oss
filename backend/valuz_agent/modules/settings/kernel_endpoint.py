"""Persisted **kernel endpoint** configuration — the "configure sandbox
address" feature.

The host can drive its kernel in-process (default) or over HTTP against a
kernel running on a SEPARATE host (a cloud sandbox, e.g. Tencent AGS). The
endpoint of that remote kernel is normally an env contract
(``VALUZ_KERNEL_MODE`` / ``_URL`` / ``_TOKEN``); this module lets a user pin
it from the app instead and have it survive restarts, backed by the same
``valuz_app_setting`` key-value table the other preferences use (the bearer
token goes in the secret store, never the DB).

Precedence (see ``apply_persisted_endpoint``): an explicit env/provisioned
``http`` mode always wins — persisted config only takes effect when the host
would otherwise run in-process. Changes apply on the next (re)start; the live
transport is not swapped mid-session.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.infra.auth_context import require_current_user_id
from valuz_agent.infra.time_utils import now_ms
from valuz_agent.modules.settings.datastore import SettingsDatastore
from valuz_agent.modules.settings.models import AppSettingRow

logger = logging.getLogger(__name__)

KEY_MODE = "kernel.endpoint.mode"
KEY_URL = "kernel.endpoint.url"
KEY_HOST_EXTERNAL_URL = "kernel.endpoint.host_external_url"

# Fixed secret-store ref for the single global kernel bearer token. The host
# connects to exactly one kernel, so a stable ref (rather than a per-row uuid)
# keeps the surface flat; presence of the secret IS the "token set" signal.
TOKEN_SECRET_REF = "kernel/endpoint-token"

MODE_VALUES = ("inprocess", "http")


@dataclass(frozen=True)
class KernelEndpointView:
    """What the settings API exposes — never the token itself."""

    mode: str
    url: str
    host_external_url: str | None
    token_present: bool


async def _read(db: AsyncSession, key: str) -> str | None:
    row = await SettingsDatastore(db).get_setting(require_current_user_id(), key)
    if row is None:
        return None
    try:
        data = json.loads(row.value_json or "{}")
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    value = data.get("value")
    return value if isinstance(value, str) and value else None


async def _write(db: AsyncSession, key: str, value: str) -> None:
    await SettingsDatastore(db).upsert_setting(
        require_current_user_id(),
        AppSettingRow(key=key, value_json=json.dumps({"value": value}), updated_at=now_ms()),
    )


def _secret_store() -> object:
    # Local import + construction mirrors ``api/deps._secret_store`` — the same
    # filesystem-backed store, reachable from boot (no request scope).
    from valuz_agent.infra.config import settings
    from valuz_agent.infra.secret_store import FileSecretStore

    return FileSecretStore(settings.secrets_dir)


async def get_endpoint(db: AsyncSession) -> KernelEndpointView:
    """Current persisted endpoint config (token redacted to a presence bool)."""
    mode = await _read(db, KEY_MODE) or "inprocess"
    url = await _read(db, KEY_URL) or ""
    host_external_url = await _read(db, KEY_HOST_EXTERNAL_URL)
    token_present = _secret_store().get(TOKEN_SECRET_REF) is not None  # type: ignore[attr-defined]
    return KernelEndpointView(
        mode=mode, url=url, host_external_url=host_external_url, token_present=token_present
    )


def _validate_http_url(url: str, *, field: str) -> str:
    cleaned = url.strip()
    if not cleaned.startswith(("http://", "https://")):
        raise ValueError(f"{field} must be an http(s) URL, got {url!r}")
    return cleaned


async def set_endpoint(
    db: AsyncSession,
    *,
    mode: str,
    url: str | None = None,
    host_external_url: str | None = None,
    token: str | None = None,
    clear_token: bool = False,
) -> KernelEndpointView:
    """Persist the endpoint. ``http`` mode requires a ``url``.

    Token semantics: a non-empty ``token`` is written to the secret store;
    ``clear_token=True`` removes it; otherwise the stored token is left as-is
    (so a settings save that doesn't re-enter the token keeps it).
    """
    if mode not in MODE_VALUES:
        raise ValueError(f"mode must be one of {MODE_VALUES}, got {mode!r}")

    if mode == "http":
        if not (url and url.strip()):
            raise ValueError("url is required when mode is 'http'")
        await _write(db, KEY_URL, _validate_http_url(url, field="url"))
    elif url is not None:
        await _write(db, KEY_URL, url.strip())

    await _write(db, KEY_MODE, mode)

    if host_external_url is not None:
        await _write(
            db,
            KEY_HOST_EXTERNAL_URL,
            _validate_http_url(host_external_url, field="host_external_url")
            if host_external_url.strip()
            else "",
        )

    store = _secret_store()
    if clear_token:
        store.delete(TOKEN_SECRET_REF)  # type: ignore[attr-defined]
    elif token and token.strip():
        store.put(TOKEN_SECRET_REF, token.strip())  # type: ignore[attr-defined]

    return await get_endpoint(db)


async def apply_persisted_endpoint(db: AsyncSession) -> bool:
    """At boot: apply a persisted ``http`` endpoint to the live ``settings``.

    Returns ``True`` when it switched the host to http mode (caller then
    rebinds the kernel client). No-op (returns ``False``) when the host is
    already http (an explicit env / provisioned sandbox wins — never clobber
    it), when persisted mode isn't ``http``, or when the url is missing.
    """
    from valuz_agent.infra.config import settings

    if settings.is_http_kernel:
        return False  # explicit env / provisioned sandbox already won

    view = await get_endpoint(db)
    if view.mode != "http" or not view.url:
        return False

    settings.kernel_mode = "http"
    settings.kernel_url = view.url
    token = _secret_store().get(TOKEN_SECRET_REF)  # type: ignore[attr-defined]
    if token:
        settings.kernel_token = token
    if view.host_external_url:
        settings.host_external_url = view.host_external_url
    logger.info("applied persisted kernel endpoint: http %s", view.url)
    return True
