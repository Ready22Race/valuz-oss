"""Persisted **sandbox driver** configuration — UI-driven remote sandbox.

The remote-sandbox (AGS) feature is normally turned on by env
(``VALUZ_SANDBOX_DRIVER`` + the ``VALUZ_AGS_*`` / ``VALUZ_COS_*`` knobs). That
"hidden env switch + scripts" flow is poor for the testing/delivery phase, so
this module lets a user configure it **once, persisted** (app settings +
secret store), and have the host provision the AGS kernel from that config at
the next (re)start — no env, no scripts.

Layout: non-secret fields live in the ``valuz_app_setting`` K-V table; the
secrets (AGS API key + kernel token, COS SecretId / SecretKey) live in the OS
secret store, never the DB. ``apply_to_settings``
copies the persisted config onto the ``settings`` singleton at boot so the
existing driver + provision path runs unchanged; an explicit env always wins.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.infra.auth_context import require_current_user_id
from valuz_agent.infra.secret_store import SecretStorePort
from valuz_agent.infra.time_utils import now_ms
from valuz_agent.modules.settings.datastore import SettingsDatastore
from valuz_agent.modules.settings.models import AppSettingRow

logger = logging.getLogger("valuz_agent.sandbox")

# Non-secret fields → valuz_app_setting.
KEY_DRIVER = "kernel.sandbox.driver"  # "inprocess" | "ags"
KEY_AGS_DOMAIN = "kernel.sandbox.ags_domain"
KEY_AGS_TEMPLATE = "kernel.sandbox.ags_template"
KEY_AGS_MOUNT_PATH = "kernel.sandbox.ags_mount_path"
KEY_COS_BUCKET = "kernel.sandbox.cos_bucket"
KEY_COS_REGION = "kernel.sandbox.cos_region"
KEY_COS_ENDPOINT = "kernel.sandbox.cos_endpoint"

# Secrets → secret store (fixed refs; presence = "set").
REF_AGS_API_KEY = "kernel/ags-api-key"
REF_AGS_KERNEL_TOKEN = "kernel/ags-kernel-token"
REF_COS_SECRET_ID = "kernel/cos-secret-id"
REF_COS_SECRET_KEY = "kernel/cos-secret-key"

DRIVER_VALUES = ("inprocess", "ags")


@dataclass(frozen=True)
class SandboxConfigView:
    """What the settings API exposes — secrets redacted to presence bools."""

    driver: str
    ags_domain: str
    ags_template: str
    ags_mount_path: str
    cos_bucket: str
    cos_region: str
    cos_endpoint: str
    ags_api_key_present: bool
    ags_kernel_token_present: bool
    cos_secret_id_present: bool
    cos_secret_key_present: bool


def _secret_store() -> SecretStorePort:
    from valuz_agent.infra.config import settings
    from valuz_agent.infra.secret_store import FileSecretStore

    return FileSecretStore(settings.secrets_dir)


async def _read(db: AsyncSession, key: str) -> str:
    import json

    row = await SettingsDatastore(db).get_setting(require_current_user_id(), key)
    if row is None:
        return ""
    try:
        data = json.loads(row.value_json or "{}")
    except (TypeError, ValueError):
        return ""
    value = data.get("value") if isinstance(data, dict) else None
    return value if isinstance(value, str) else ""


async def _write(db: AsyncSession, key: str, value: str) -> None:
    import json

    await SettingsDatastore(db).upsert_setting(
        require_current_user_id(),
        AppSettingRow(key=key, value_json=json.dumps({"value": value}), updated_at=now_ms()),
    )


async def get_sandbox_config(db: AsyncSession) -> SandboxConfigView:
    store = _secret_store()
    return SandboxConfigView(
        driver=(await _read(db, KEY_DRIVER)) or "inprocess",
        ags_domain=await _read(db, KEY_AGS_DOMAIN),
        ags_template=await _read(db, KEY_AGS_TEMPLATE),
        ags_mount_path=(await _read(db, KEY_AGS_MOUNT_PATH)) or "/workspace",
        cos_bucket=await _read(db, KEY_COS_BUCKET),
        cos_region=(await _read(db, KEY_COS_REGION)) or "ap-beijing",
        cos_endpoint=await _read(db, KEY_COS_ENDPOINT),
        ags_api_key_present=store.get(REF_AGS_API_KEY) is not None,
        ags_kernel_token_present=store.get(REF_AGS_KERNEL_TOKEN) is not None,
        cos_secret_id_present=store.get(REF_COS_SECRET_ID) is not None,
        cos_secret_key_present=store.get(REF_COS_SECRET_KEY) is not None,
    )


async def set_sandbox_config(
    db: AsyncSession,
    *,
    driver: str | None = None,
    ags_domain: str | None = None,
    ags_template: str | None = None,
    ags_mount_path: str | None = None,
    cos_bucket: str | None = None,
    cos_region: str | None = None,
    cos_endpoint: str | None = None,
    ags_api_key: str | None = None,
    ags_kernel_token: str | None = None,
    cos_secret_id: str | None = None,
    cos_secret_key: str | None = None,
) -> SandboxConfigView:
    """Persist the sandbox config. Only non-None fields are written; secrets go
    to the secret store. ``driver='ags'`` requires the AGS + COS essentials."""
    if driver is not None:
        if driver not in DRIVER_VALUES:
            raise ValueError(f"driver must be one of {DRIVER_VALUES}, got {driver!r}")
        await _write(db, KEY_DRIVER, driver)
    for key, val in (
        (KEY_AGS_DOMAIN, ags_domain),
        (KEY_AGS_TEMPLATE, ags_template),
        (KEY_AGS_MOUNT_PATH, ags_mount_path),
        (KEY_COS_BUCKET, cos_bucket),
        (KEY_COS_REGION, cos_region),
        (KEY_COS_ENDPOINT, cos_endpoint),
    ):
        if val is not None:
            await _write(db, key, val.strip())

    store = _secret_store()
    for ref, secret in (
        (REF_AGS_API_KEY, ags_api_key),
        (REF_AGS_KERNEL_TOKEN, ags_kernel_token),
        (REF_COS_SECRET_ID, cos_secret_id),
        (REF_COS_SECRET_KEY, cos_secret_key),
    ):
        if secret:  # non-empty → write; empty string clears
            store.put(ref, secret.strip())
        elif secret == "":
            store.delete(ref)

    return await get_sandbox_config(db)


async def apply_to_settings(db: AsyncSession) -> str | None:
    """Copy a persisted ``ags`` config onto the ``settings`` singleton (incl.
    secrets) so the boot provision path can run from it. Returns the driver
    name to provision (``"ags"``), or ``None`` when nothing to do.

    No-op when an explicit env already selected a driver, or when the persisted
    driver isn't ``ags``, or when the AGS/COS essentials are missing.
    """
    import os

    from valuz_agent.infra.config import settings

    if os.environ.get("VALUZ_SANDBOX_DRIVER"):  # explicit env wins
        return None

    cfg = await get_sandbox_config(db)
    if cfg.driver != "ags":
        return None

    store = _secret_store()
    api_key = store.get(REF_AGS_API_KEY)
    if not (api_key and cfg.ags_template and cfg.cos_bucket):
        logger.warning(
            "persisted sandbox driver is 'ags' but config is incomplete "
            "(api_key/template/bucket) — running in-process"
        )
        return None

    settings.ags_api_key = api_key
    settings.ags_domain = cfg.ags_domain or None
    settings.ags_kernel_template = cfg.ags_template
    settings.ags_mount_path = cfg.ags_mount_path
    # The sandbox panel's own token, else whatever env/default is on settings.
    settings.ags_kernel_token = store.get(REF_AGS_KERNEL_TOKEN) or settings.ags_kernel_token
    settings.cos_bucket = cfg.cos_bucket
    settings.cos_region = cfg.cos_region
    settings.cos_endpoint = cfg.cos_endpoint or None
    settings.cos_secret_id = store.get(REF_COS_SECRET_ID)
    settings.cos_secret_key = store.get(REF_COS_SECRET_KEY)
    return "ags"


async def apply_cos_to_settings(db: AsyncSession) -> bool:
    """Copy the persisted COS config (bucket / region / endpoint + secrets) onto
    the ``settings`` singleton, unconditionally.

    Unlike ``apply_to_settings`` (which is the BOOT driver-selection path and
    defers to an explicit ``VALUZ_SANDBOX_DRIVER`` env), this is for the
    **sync-workspace action**: the user configured COS in the UI and clicked
    sync, so their persisted config must reach ``cos_object_store()`` regardless
    of how the kernel driver was selected (env-launched ``make dev-ags`` or
    pure-UI ``make dev``). Persisted values fill in; anything the UI left blank
    keeps whatever env already set. Returns True once bucket + both secrets are
    present.
    """
    from valuz_agent.infra.config import settings

    cfg = await get_sandbox_config(db)
    store = _secret_store()
    if cfg.cos_bucket:
        settings.cos_bucket = cfg.cos_bucket
    if cfg.cos_region:
        settings.cos_region = cfg.cos_region
    if cfg.cos_endpoint:
        settings.cos_endpoint = cfg.cos_endpoint
    if (sid := store.get(REF_COS_SECRET_ID)) is not None:
        settings.cos_secret_id = sid
    if (sk := store.get(REF_COS_SECRET_KEY)) is not None:
        settings.cos_secret_key = sk
    return bool(settings.cos_bucket and settings.cos_secret_id and settings.cos_secret_key)
