from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.api.deps import _secret_store, get_settings_service, require_current_user_id
from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.infra.eventbus import event_bus
from valuz_agent.modules.providers.datastore import ProviderDatastore
from valuz_agent.modules.providers.errors import NoAvailableProvider, ProviderNotFound
from valuz_agent.modules.providers.service import ProviderService
from valuz_agent.modules.settings.model_options import (
    CurrentDefault,
    ModelOptionsResponse,
    build_model_options,
    to_option_input,
)
from valuz_agent.modules.settings.preferences import (
    detect_system_timezone,
    get_data_api_kind,
    get_data_api_token,
    get_data_api_url,
    get_default_effort,
    get_default_locale,
    get_default_model,
    get_default_provider_id,
    get_default_runtime,
    get_default_timezone,
    get_durable_database_url,
    get_font_size,
    get_kernel_store,
    get_theme,
    set_data_api_kind,
    set_data_api_token,
    set_data_api_url,
    set_default_effort,
    set_default_locale,
    set_default_model,
    set_default_provider_id,
    set_default_runtime,
    set_default_timezone,
    set_durable_database_url,
    set_font_size,
    set_kernel_store,
    set_theme,
)
from valuz_agent.modules.settings.service import (
    AboutInfo,
    CapabilitiesSnapshot,
    SettingsService,
    UpdateCheckResult,
)
from valuz_agent.ports.extensions import ext
from valuz_agent.ports.llm_provider import SystemProviderImmutable

router = APIRouter(prefix="/v1/settings", tags=["settings"])


# ── Preferences (ADR-010) ────────────────────────────────────────────


class PreferencesResponse(BaseModel):
    default_timezone: str
    default_locale: str
    detected_timezone: str
    theme: str
    font_size: str


class PreferencesPatchPayload(BaseModel):
    default_timezone: str | None = Field(default=None, min_length=1)
    default_locale: str | None = Field(default=None, min_length=1)
    theme: str | None = Field(default=None)
    font_size: str | None = Field(default=None)


async def _read_preferences(db: AsyncSession) -> PreferencesResponse:
    return PreferencesResponse(
        default_timezone=await get_default_timezone(db),
        default_locale=await get_default_locale(db),
        detected_timezone=detect_system_timezone(),
        theme=await get_theme(db),
        font_size=await get_font_size(db),
    )


@router.get("/preferences")
async def get_preferences() -> PreferencesResponse:
    """Return user-level preferences that drive schedule + UI behavior.

    ``detected_timezone`` is a UX hint, not a contract — the frontend
    can use it to seed an initial "Use system timezone" suggestion on
    first-run.
    """
    async with async_unit_of_work(commit=False) as db:
        return await _read_preferences(db)


@router.patch("/preferences")
async def patch_preferences(payload: PreferencesPatchPayload) -> PreferencesResponse:
    """Update user preferences. Only sent keys are updated."""
    try:
        async with async_unit_of_work() as db:
            if payload.default_timezone is not None:
                await set_default_timezone(db, payload.default_timezone)
            if payload.default_locale is not None:
                await set_default_locale(db, payload.default_locale)
            if payload.theme is not None:
                await set_theme(db, payload.theme)
            if payload.font_size is not None:
                await set_font_size(db, payload.font_size)
            return await _read_preferences(db)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ── Kernel data service (model-A durable write-through) ──────────────
#
# Surfaced as a regular Settings tab (Data Service). Drives the
# IN-PROCESS kernel's store tier; applied at the NEXT backend start by
# ``boot.kernel.init_kernel_dependencies`` (the store is built once at boot).
# The ``token`` is write-only on the wire: GET returns ``token_set`` (a bool),
# never the value, so the secret isn't echoed back to the client.


class DataServiceResponse(BaseModel):
    kernel_store: str  # one of KERNEL_STORE_VALUES
    durable_database_url: str
    data_api_url: str
    data_api_kind: str
    token_set: bool
    # True once the persisted config differs from what the running kernel
    # booted with — the UI shows a "restart to apply" hint. Best-effort.
    restart_required: bool


class DataServicePatchPayload(BaseModel):
    kernel_store: str | None = Field(default=None)
    durable_database_url: str | None = None
    data_api_url: str | None = None
    data_api_kind: str | None = None
    # ``None`` = leave unchanged; ``""`` = clear.
    data_api_token: str | None = None


async def _read_data_service(db: AsyncSession) -> DataServiceResponse:
    import os

    kernel_store = await get_kernel_store(db)
    durable = await get_durable_database_url(db)
    api_url = await get_data_api_url(db)
    api_kind = await get_data_api_kind(db)
    token = await get_data_api_token(db)
    # The running kernel booted from these env vars (set by init_kernel_dependencies).
    booted_store = os.environ.get("KERNEL_STORE", "local")
    booted_durable = os.environ.get("VALUZ_DURABLE_DATABASE_URL", "")
    booted_api_url = os.environ.get("VALUZ_DATA_API_URL", "")
    restart_required = (kernel_store, durable, api_url) != (
        booted_store,
        booted_durable,
        booted_api_url,
    )
    return DataServiceResponse(
        kernel_store=kernel_store,
        durable_database_url=durable,
        data_api_url=api_url,
        data_api_kind=api_kind,
        token_set=bool(token),
        restart_required=restart_required,
    )


@router.get("/data-service")
async def get_data_service() -> DataServiceResponse:
    """Return the kernel data-service (durable store) config. The bearer token
    is never echoed — only ``token_set``."""
    async with async_unit_of_work(commit=False) as db:
        return await _read_data_service(db)


@router.patch("/data-service")
async def patch_data_service(payload: DataServicePatchPayload) -> DataServiceResponse:
    """Update the kernel data-service config. Only sent keys change; takes
    effect on the next backend start."""
    try:
        async with async_unit_of_work() as db:
            if payload.kernel_store is not None:
                await set_kernel_store(db, payload.kernel_store)
            if payload.durable_database_url is not None:
                await set_durable_database_url(db, payload.durable_database_url)
            if payload.data_api_url is not None:
                await set_data_api_url(db, payload.data_api_url)
            if payload.data_api_kind is not None:
                await set_data_api_kind(db, payload.data_api_kind)
            if payload.data_api_token is not None:
                await set_data_api_token(db, payload.data_api_token)
            return await _read_data_service(db)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


class DataServiceHealthResponse(BaseModel):
    # "ok" | "error" — whether the configured durable backend is reachable.
    status: str
    # "local" | "pg" | "remote" — the configured backend kind.
    backend: str
    detail: str


async def _probe_data_service(db: AsyncSession) -> DataServiceHealthResponse:
    """Probe the CONFIGURED durable backend (not the currently-booted one) so the
    panel reflects what a restart would use. local → always ok; pg → connect +
    SELECT 1; remote → GET the data-API ``/health``."""
    kernel_store = await get_kernel_store(db)
    if kernel_store == "pg":
        dsn = await get_durable_database_url(db)
        if not dsn:
            return DataServiceHealthResponse(
                status="error", backend="pg", detail="Postgres DSN not configured"
            )
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine(dsn)
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return DataServiceHealthResponse(status="ok", backend="pg", detail="Postgres reachable")
        except Exception as exc:  # noqa: BLE001 — surfaced to the UI as a status
            return DataServiceHealthResponse(status="error", backend="pg", detail=str(exc)[:200])
        finally:
            await engine.dispose()
    if kernel_store == "remote":
        base = await get_data_api_url(db)
        if not base:
            return DataServiceHealthResponse(
                status="error", backend="remote", detail="data-API URL not configured"
            )
        import httpx

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(base.rstrip("/") + "/health")
            if resp.status_code < 400:
                return DataServiceHealthResponse(
                    status="ok", backend="remote", detail=f"data service reachable ({base})"
                )
            return DataServiceHealthResponse(
                status="error", backend="remote", detail=f"HTTP {resp.status_code} from {base}"
            )
        except Exception as exc:  # noqa: BLE001 — surfaced to the UI as a status
            return DataServiceHealthResponse(
                status="error", backend="remote", detail=str(exc)[:200]
            )
    return DataServiceHealthResponse(
        status="ok", backend="local", detail="host-managed SQLite (always available)"
    )


@router.get("/data-service/health")
async def get_data_service_health() -> DataServiceHealthResponse:
    """Live health of the configured durable backend (sqlite / Postgres / remote
    data service), for the Data Service settings panel."""
    async with async_unit_of_work(commit=False) as db:
        return await _probe_data_service(db)


# ── Model defaults (runtime + provider + model + effort) ─────────────


class ModelDefaultsResponse(BaseModel):
    default_runtime: str  # one of RUNTIME_VALUES
    default_provider_id: str | None
    default_model: str | None
    # Kernel V5+bba3014 ``ModelSettings.effort`` — always one of
    # ``low|medium|high|xhigh|max``. The Composer's old "Default"
    # sentinel is gone; unset / cleared rows collapse to
    # ``FALLBACK_EFFORT`` ("high") at read time so the dropdown is
    # never empty.
    default_effort: str


class ModelDefaultsPatchPayload(BaseModel):
    default_runtime: str | None = Field(default=None, min_length=1)
    # Empty string clears the field (e.g. when the user switches runtime
    # and the previous default isn't compatible). ``None`` means "don't
    # touch this key" — required so the UI can update provider+model
    # together without nuking effort/runtime.
    default_provider_id: str | None = None
    default_model: str | None = None
    # Effort accepts one of EFFORT_VALUES, or the empty string (=
    # legacy clear; now reset to FALLBACK_EFFORT). ``None`` means
    # "don't touch this key".
    default_effort: str | None = None


async def _read_model_defaults(db: AsyncSession) -> ModelDefaultsResponse:
    return ModelDefaultsResponse(
        default_runtime=await get_default_runtime(db),
        default_provider_id=await get_default_provider_id(db),
        default_model=await get_default_model(db),
        default_effort=await get_default_effort(db),
    )


async def _mirror_to_default_assistant(
    user_id: str, db: AsyncSession, defaults: ModelDefaultsResponse
) -> None:
    """09-assistant: the 默认助手 base agent's brain mirrors the global model
    default (Settings = source of truth). Keeps the always-present default
    conversation agent on the user's chosen runtime/model/effort. Re-syncs its
    kernel AgentConfig via ``update_agent`` so live sessions pick it up."""
    from valuz_agent.modules.agents.seed import DEFAULT_ASSISTANT_SLUG
    from valuz_agent.modules.agents.service import AgentNotFoundError, AgentService

    try:
        await AgentService(db).update_agent(  # type: ignore[arg-type]
            user_id,
            DEFAULT_ASSISTANT_SLUG,
            {
                "runtime": defaults.default_runtime,
                "model": defaults.default_model,
                "provider_id": defaults.default_provider_id,
                "effort": defaults.default_effort,
            },
        )
    except AgentNotFoundError:
        # Not seeded yet (fresh DB before the boot seeder) — nothing to mirror.
        pass


async def _finish_model_defaults(user_id: str, db: AsyncSession) -> ModelDefaultsResponse:
    defaults = await _read_model_defaults(db)
    await _mirror_to_default_assistant(user_id, db, defaults)
    return defaults


@router.get("/model-defaults")
async def get_model_defaults() -> ModelDefaultsResponse:
    """Return the global model-default tuple that drives quick-chat and
    scheduled tasks. The four fields together pin one specific
    (runtime, provider, model, effort) combination — the frontend
    "Default" card writes back any subset on change.
    """
    async with async_unit_of_work(commit=False) as db:
        return await _read_model_defaults(db)


@router.patch("/model-defaults")
async def patch_model_defaults(
    payload: ModelDefaultsPatchPayload,
    user_id: str = Depends(require_current_user_id),
) -> ModelDefaultsResponse:
    """Update the global model-default tuple.

    ``default_provider_id`` behaviour:
    - Non-empty string: delegates to ``ProviderService.set_default`` so that
      the provider row's ``is_default`` flag and ``default_model`` are updated
      atomically alongside the app-setting keys.  This is the path model_resolver
      reads (``providers.get_default()``) and ensures settings-page changes are
      immediately visible to scheduled tasks and quick-chat sessions.
    - Empty string ``""``: clears the default — resets all ``is_default`` flags
      via ``ProviderDatastore.clear_default()`` and writes ``None`` to both
      app-setting keys.
    - ``None``: no change to provider/model defaults (only runtime/effort may change).
    """
    try:
        async with async_unit_of_work() as db:
            if payload.default_runtime is not None:
                await set_default_runtime(db, payload.default_runtime)

            if payload.default_provider_id is not None:
                if payload.default_provider_id == "":
                    # Clear: wipe is_default on all rows + clear app-setting keys.
                    ds = ProviderDatastore(db)
                    await ds.clear_default(user_id)
                    await set_default_provider_id(db, None)
                    await set_default_model(db, None)
                elif any(
                    it.id == payload.default_provider_id for it in await ext.llm_provider.list()
                ):
                    # Contributed (catalog) channel (e.g. the commercial
                    # "Valuz 系统模型" channel — ADR-011). It has no providers-table
                    # row to carry ``is_default``, so pin it via preferences only —
                    # NOT through ProviderService.set_default, whose
                    # ``_guard_not_system`` correctly blocks editing system
                    # providers but over-blocks selecting one as the default. Clear
                    # any builtin row's ``is_default`` so model_resolver doesn't see
                    # two defaults.
                    await ProviderDatastore(db).clear_default(user_id)
                    await set_default_provider_id(db, payload.default_provider_id)
                    if payload.default_model is not None:
                        await set_default_model(db, payload.default_model or None)
                else:
                    # Set: delegate to ProviderService so is_default +
                    # default_model row + app-setting keys all update together.
                    svc = ProviderService(
                        datastore=ProviderDatastore(db),
                        secret_store=_secret_store(),
                        event_bus=event_bus,
                    )
                    await svc.set_default(
                        user_id,
                        payload.default_provider_id,
                        default_model=payload.default_model or None,
                    )
                    # default_model already synced inside set_default; skip the
                    # standalone write below so we don't double-write.
                    return await _finish_model_defaults(user_id, db)
            elif payload.default_model is not None:
                # Provider not being changed — still honour a standalone
                # default_model update (e.g. user picks a different model
                # on the same provider).
                await set_default_model(db, payload.default_model or None)

            if payload.default_effort is not None:
                # Empty string is treated as "reset to FALLBACK_EFFORT"
                # by ``set_default_effort``; concrete values are
                # validated against EFFORT_VALUES.
                await set_default_effort(db, payload.default_effort or None)
            return await _finish_model_defaults(user_id, db)
    except SystemProviderImmutable as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "reason": "system-managed provider is read-only",
                "provider_id": exc.provider_id,
            },
        ) from exc
    except ProviderNotFound as exc:
        raise HTTPException(status_code=404, detail={"reason": str(exc)}) from exc
    except NoAvailableProvider as exc:
        raise HTTPException(status_code=422, detail={"reason": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ── Model options (read model for the "pick a default model" pickers) ──


@router.get("/model-options")
async def get_model_options(
    user_id: str = Depends(require_current_user_id),
) -> ModelOptionsResponse:
    """Return the grouped, fully-resolved model options for the default-model
    pickers (onboarding's ConnectStep + Settings default-config card).

    Distinct from ``GET /v1/providers`` (provider management): every model here
    carries the ``runtimes`` it can run on + a preferred ``default_runtime``,
    same-named models inside a provider are disambiguated, and a system channel
    collapses to a single provider. The picker UIs render this verbatim — they
    no longer derive a runtime client-side.

    Subscription providers come back ``status="client_resolved"``: their CLI
    login state lives in the local keychain (invisible to the server), so the
    client fills availability in from its own ``checkCliLogin`` probe.
    """
    async with async_unit_of_work(commit=False) as db:
        defaults = await _read_model_defaults(db)
        svc = ProviderService(
            datastore=ProviderDatastore(db),
            secret_store=_secret_store(),
            event_bus=event_bus,
        )
        items = await svc.list_providers(user_id)
        # ADR-011: each model carries its declared ``runtimes`` (or None →
        # derive from compatible_protocols); the builder reads them straight off,
        # no per-source special-casing.
        inputs = [to_option_input(it) for it in items]
        current = CurrentDefault(
            runtime=defaults.default_runtime,
            provider_id=defaults.default_provider_id,
            model=defaults.default_model,
        )
        return build_model_options(inputs, current)


@router.get("")
async def get_settings(
    svc: SettingsService = Depends(get_settings_service),
) -> dict[str, Any]:
    return await svc.get_app_settings()


@router.patch("")
async def patch_settings(
    updates: dict[str, Any],
    svc: SettingsService = Depends(get_settings_service),
) -> dict[str, Any]:
    return await svc.patch_app_settings(updates)


@router.get("/capabilities")
async def get_capabilities(
    svc: SettingsService = Depends(get_settings_service),
) -> CapabilitiesSnapshot:
    return await svc.derive_capabilities()


@router.post("/onboarding/complete")
async def complete_onboarding(
    svc: SettingsService = Depends(get_settings_service),
) -> dict[str, bool]:
    await svc.patch_onboarding(completed=True)
    return {"completed": True}


@router.get("/shortcuts")
async def list_shortcuts(
    svc: SettingsService = Depends(get_settings_service),
) -> dict[str, list[dict[str, Any]]]:
    return {"shortcuts": await svc.list_shortcuts()}


@router.patch("/shortcuts")
async def patch_shortcuts(
    updates: list[dict[str, Any]],
    svc: SettingsService = Depends(get_settings_service),
) -> dict[str, list[dict[str, Any]]]:
    return {"shortcuts": await svc.patch_shortcuts(updates)}


@router.post("/shortcuts/reset")
async def reset_shortcuts(
    svc: SettingsService = Depends(get_settings_service),
) -> dict[str, list[dict[str, Any]]]:
    return {"shortcuts": await svc.reset_shortcuts()}


@router.get("/about")
async def get_about(
    svc: SettingsService = Depends(get_settings_service),
) -> AboutInfo:
    return await svc.get_about_info()


@router.post("/about/check-updates")
async def check_updates(
    svc: SettingsService = Depends(get_settings_service),
) -> UpdateCheckResult:
    return await svc.check_updates()
