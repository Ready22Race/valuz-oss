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
from valuz_agent.infra.secret_store import SecretStorePort
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


def _secret_store() -> SecretStorePort:
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
    token_present = _secret_store().get(TOKEN_SECRET_REF) is not None
    return KernelEndpointView(
        mode=mode, url=url, host_external_url=host_external_url, token_present=token_present
    )


def stored_token() -> str | None:
    """The persisted bearer token, if any (for "Test" on a saved endpoint)."""
    return _secret_store().get(TOKEN_SECRET_REF)


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
        store.delete(TOKEN_SECRET_REF)
    elif token and token.strip():
        store.put(TOKEN_SECRET_REF, token.strip())

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
    token = _secret_store().get(TOKEN_SECRET_REF)
    if token:
        settings.kernel_token = token
    if view.host_external_url:
        settings.host_external_url = view.host_external_url
    logger.info("applied persisted kernel endpoint: http %s", view.url)
    return True


# ── "Test remote" probe ───────────────────────────────────────────────
#
# A pre-save readiness check, modelled on Hermes Desktop's "Test remote".
# The load-bearing lesson there: a shallow ``/health`` "ready" check that
# doesn't exercise what a live session actually needs gives a false green and
# a flap loop. For us the live session needs BOTH directions:
#   • host → kernel (control/events): probed for real — reachable + auth.
#   • kernel → host (④ tool-callback): cannot be exercised without a running
#     session, but its #1 failure mode (a loopback callback base a remote
#     kernel can't reach) IS statically detectable — so the probe returns it
#     as an explicit hint rather than pretending the endpoint is fully wired.


def _callback_hint(host_external_url: str | None) -> str:
    """Classify the ④ callback base: ``unset`` / ``loopback`` / ``ok``."""
    base = (host_external_url or "").strip()
    if not base:
        return "unset"
    if "127.0.0.1" in base or "localhost" in base or "[::1]" in base:
        return "loopback"
    return "ok"


@dataclass(frozen=True)
class ProbeResult:
    kernel_reachable: bool  # host → kernel TCP/HTTP reached at all
    auth_ok: bool  # the bearer token is accepted (authed read returned non-401)
    kernel_status: int | None  # HTTP status of the authed read, if reached
    callback_hint: str  # ④ direction: unset | loopback | ok (static)
    ok: bool  # host→kernel fully good (reachable + auth)
    detail: str  # human-readable summary / first error


async def probe_endpoint(
    *,
    url: str,
    token: str | None,
    host_external_url: str | None = None,
    transport: object | None = None,
) -> ProbeResult:
    """Probe a candidate remote-kernel endpoint before the user commits to it.

    ``transport`` is an optional ``httpx`` transport for tests. The authed read
    targets the owner-scoped sessions list, so it carries the same headers a
    real host call does (bearer + ``X-Valuz-Owner-Id``); a 401 means a bad
    token, a 200 means the kernel serves owner-scoped data for this host.
    """
    import httpx

    base = url.strip().rstrip("/")
    hint = _callback_hint(host_external_url)
    headers = {"X-Valuz-Owner-Id": require_current_user_id()}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        async with httpx.AsyncClient(timeout=5.0, transport=transport) as client:  # type: ignore[arg-type]
            resp = await client.get(f"{base}/api/v1/sessions", headers=headers)
    except Exception as exc:  # noqa: BLE001 — any transport error = unreachable
        return ProbeResult(
            kernel_reachable=False,
            auth_ok=False,
            kernel_status=None,
            callback_hint=hint,
            ok=False,
            detail=f"kernel not reachable at {base}: {exc}",
        )

    status = resp.status_code
    auth_ok = status != 401
    ok = auth_ok and status < 500
    if status == 401:
        detail = "reached the kernel, but the token was rejected (401)"
    elif status >= 500:
        detail = f"kernel reachable but returned {status}"
    else:
        detail = f"kernel reachable and token accepted ({status})"
    return ProbeResult(
        kernel_reachable=True,
        auth_ok=auth_ok,
        kernel_status=status,
        callback_hint=hint,
        ok=ok,
        detail=detail,
    )
