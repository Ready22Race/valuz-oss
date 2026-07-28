from __future__ import annotations

import json
from dataclasses import dataclass
from json import JSONDecodeError
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from valuz_agent.api.deps import get_channel_ingress_service, get_current_user_id
from valuz_agent.infra import secret_store
from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.integrations.feishu_long_connection import feishu_supervisor
from valuz_agent.integrations.wecom_aibot_long_connection import wecom_aibot_supervisor
from valuz_agent.modules.channels.adapters import (
    ChannelVerificationError,
    FeishuChannelAdapter,
    FeishuChannelConfig,
    FeishuUrlVerificationResponse,
    InboundChannelMessage,
    WeComChannelAdapter,
)
from valuz_agent.modules.channels.config import (
    ChannelConfigError,
    load_wecom_aibot_config,
    load_wecom_config,
    read_wecom_aibot_binding,
)
from valuz_agent.modules.channels.datastore import (
    AgentChannelBindingDatastore,
    ChannelChatBindingDatastore,
)
from valuz_agent.modules.channels.schemas import AgentChannelBinding
from valuz_agent.modules.channels.service import ChannelIngressService

router = APIRouter(prefix="/v1/channels", tags=["channels"])
FEISHU_PLATFORM = "feishu"
WECOM_AIBOT_PLATFORM = "wecom_aibot"


class WeComAIBotBindingResponse(BaseModel):
    enabled: bool
    channel_instance_id: str
    owner_user_id: str
    agent_slug: str
    bot_id: str
    has_secret: bool
    connected: bool = False
    connection_status: str = "stopped"
    connection_error: str | None = None


class WeComAIBotBindingUpdate(BaseModel):
    enabled: bool = True
    channel_instance_id: str | None = Field(default=None, min_length=1)
    agent_slug: str = Field(min_length=1)
    bot_id: str = Field(min_length=1)
    secret: str | None = None


class FeishuBindingResponse(BaseModel):
    enabled: bool
    channel_instance_id: str
    owner_user_id: str
    agent_slug: str
    app_id: str
    has_app_secret: bool
    has_verification_token: bool
    has_encrypt_key: bool
    connected: bool = False
    connection_status: str = "stopped"
    connection_error: str | None = None


class FeishuBindingUpdate(BaseModel):
    enabled: bool = True
    channel_instance_id: str | None = Field(default=None, min_length=1)
    agent_slug: str = Field(min_length=1)
    app_id: str = Field(min_length=1)
    app_secret: str | None = None
    verification_token: str | None = None
    encrypt_key: str | None = None


@dataclass(frozen=True, slots=True)
class _FeishuSecretPayload:
    app_secret: str | None = None
    verification_token: str | None = None
    encrypt_key: str | None = None


@router.get("/wecom-aibot/bindings/{agent_slug}", response_model=WeComAIBotBindingResponse)
async def get_wecom_aibot_binding(
    agent_slug: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> WeComAIBotBindingResponse:
    async with async_unit_of_work() as db:
        binding = await AgentChannelBindingDatastore(db).get(
            user_id=user_id,
            platform=WECOM_AIBOT_PLATFORM,
            agent_slug=agent_slug,
        )
    if binding is None:
        legacy = read_wecom_aibot_binding()
        if legacy.agent_slug == agent_slug and legacy.bot_id:
            runtime = wecom_aibot_supervisor.status_for(agent_slug)
            return WeComAIBotBindingResponse(
                enabled=legacy.enabled,
                channel_instance_id=legacy.channel_instance_id,
                owner_user_id=legacy.owner_user_id or user_id,
                agent_slug=legacy.agent_slug,
                bot_id=legacy.bot_id,
                has_secret=legacy.has_secret,
                connected=runtime.connected,
                connection_status=runtime.status,
                connection_error=runtime.last_error,
            )
    return _wecom_aibot_binding_response(user_id=user_id, agent_slug=agent_slug, binding=binding)


@router.put("/wecom-aibot/bindings/{agent_slug}", response_model=WeComAIBotBindingResponse)
async def update_wecom_aibot_binding(
    agent_slug: str,
    body: WeComAIBotBindingUpdate,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> WeComAIBotBindingResponse:
    body_agent_slug = body.agent_slug.strip()
    if body_agent_slug != agent_slug:
        raise HTTPException(status_code=400, detail="agent_slug mismatch")
    async with async_unit_of_work() as db:
        datastore = AgentChannelBindingDatastore(db)
        existing = await datastore.get(
            user_id=user_id,
            platform=WECOM_AIBOT_PLATFORM,
            agent_slug=agent_slug,
        )
        secret_ref = existing.secret_ref if existing is not None else None
        supplied_secret = body.secret.strip() if body.secret and body.secret.strip() else None
        if supplied_secret:
            secret_ref = _wecom_aibot_secret_ref(agent_slug)
            secret_store.put(user_id, secret_ref, supplied_secret)
        elif not secret_ref or not secret_store.get(user_id, secret_ref):
            legacy_secret = _legacy_wecom_aibot_secret(agent_slug)
            if legacy_secret:
                secret_ref = _wecom_aibot_secret_ref(agent_slug)
                secret_store.put(user_id, secret_ref, legacy_secret)
            else:
                raise HTTPException(status_code=422, detail="Secret is required")
        binding = await datastore.upsert(
            user_id=user_id,
            platform=WECOM_AIBOT_PLATFORM,
            agent_slug=agent_slug,
            channel_instance_id=body.channel_instance_id or "wecom-aibot-main",
            bot_id=body.bot_id.strip(),
            secret_ref=secret_ref,
            enabled=body.enabled,
        )
    await wecom_aibot_supervisor.restart()
    return _wecom_aibot_binding_response(user_id=user_id, agent_slug=agent_slug, binding=binding)


@router.get("/feishu/bindings/{agent_slug}", response_model=FeishuBindingResponse)
async def get_feishu_binding(
    agent_slug: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> FeishuBindingResponse:
    async with async_unit_of_work() as db:
        binding = await AgentChannelBindingDatastore(db).get(
            user_id=user_id,
            platform=FEISHU_PLATFORM,
            agent_slug=agent_slug,
        )
    return _feishu_binding_response(user_id=user_id, agent_slug=agent_slug, binding=binding)


@router.put("/feishu/bindings/{agent_slug}", response_model=FeishuBindingResponse)
async def update_feishu_binding(
    agent_slug: str,
    body: FeishuBindingUpdate,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> FeishuBindingResponse:
    body_agent_slug = body.agent_slug.strip()
    if body_agent_slug != agent_slug:
        raise HTTPException(status_code=400, detail="agent_slug mismatch")

    async with async_unit_of_work() as db:
        datastore = AgentChannelBindingDatastore(db)
        existing = await datastore.get(
            user_id=user_id,
            platform=FEISHU_PLATFORM,
            agent_slug=agent_slug,
        )
        secret_ref = (
            existing.secret_ref
            if existing is not None and existing.secret_ref
            else _feishu_secret_ref(agent_slug)
        )
        existing_secret = _read_feishu_secret(user_id=user_id, secret_ref=secret_ref)
        app_secret = _coalesce_secret_value(
            body.app_secret,
            existing_secret.app_secret,
        )
        verification_token = _coalesce_secret_value(
            body.verification_token,
            existing_secret.verification_token,
        )
        encrypt_key = _coalesce_secret_value(body.encrypt_key, existing_secret.encrypt_key)
        if not app_secret:
            raise HTTPException(status_code=422, detail="App Secret is required")
        _write_feishu_secret(
            user_id=user_id,
            secret_ref=secret_ref,
            payload=_FeishuSecretPayload(
                app_secret=app_secret,
                verification_token=verification_token,
                encrypt_key=encrypt_key,
            ),
        )
        binding = await datastore.upsert(
            user_id=user_id,
            platform=FEISHU_PLATFORM,
            agent_slug=agent_slug,
            channel_instance_id=(body.channel_instance_id or "feishu-main").strip(),
            bot_id=body.app_id.strip(),
            secret_ref=secret_ref,
            enabled=body.enabled,
        )
    await feishu_supervisor.restart()
    return _feishu_binding_response(user_id=user_id, agent_slug=agent_slug, binding=binding)


class FeishuBindingTestResponse(BaseModel):
    credential_ok: bool
    error: str | None = None
    connected: bool = False
    connection_status: str = "stopped"
    connection_error: str | None = None


FEISHU_TENANT_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"


async def _check_feishu_credentials(app_id: str, app_secret: str) -> tuple[bool, str | None]:
    """Exchange the app credentials for a tenant access token — the cheapest
    call that proves app_id/app_secret are valid and Feishu is reachable."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                FEISHU_TENANT_TOKEN_URL,
                json={"app_id": app_id, "app_secret": app_secret},
            )
        data: dict[str, Any] = resp.json()
    except Exception as exc:  # noqa: BLE001 — an unreachable Feishu is a test result
        return False, str(exc)
    if resp.status_code >= 400 or data.get("code") != 0:
        return False, str(data.get("msg") or f"HTTP {resp.status_code}")
    return True, None


@router.post(
    "/feishu/bindings/{agent_slug}/test",
    response_model=FeishuBindingTestResponse,
)
async def test_feishu_binding(
    agent_slug: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> FeishuBindingTestResponse:
    """One-click health probe: validate the stored app credentials against
    Feishu and report the live long-connection status."""
    async with async_unit_of_work() as db:
        binding = await AgentChannelBindingDatastore(db).get(
            user_id=user_id,
            platform=FEISHU_PLATFORM,
            agent_slug=agent_slug,
        )
    if binding is None:
        raise HTTPException(status_code=404, detail="feishu binding not found")
    secret = _read_feishu_secret(user_id=user_id, secret_ref=binding.secret_ref)
    if not secret.app_secret:
        raise HTTPException(status_code=422, detail="App Secret is required")
    credential_ok, error = await _check_feishu_credentials(
        binding.bot_id, secret.app_secret
    )
    runtime = feishu_supervisor.status_for(agent_slug)
    return FeishuBindingTestResponse(
        credential_ok=credential_ok,
        error=error,
        connected=runtime.connected,
        connection_status=runtime.status,
        connection_error=runtime.last_error,
    )


class ChannelChatItem(BaseModel):
    """A chat the bot can be bound to (i.e. it is already a member)."""

    external_chat_id: str
    name: str
    bound_project_id: str | None = None


class ChatProjectBindingResponse(BaseModel):
    channel_instance_id: str
    external_chat_id: str
    project_id: str
    external_chat_name: str | None = None
    default_agent_slug: str | None = None


class ChatProjectBindingUpdate(BaseModel):
    channel_instance_id: str = Field(default="feishu-main", min_length=1)
    external_chat_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    external_chat_name: str | None = None
    default_agent_slug: str | None = None


@router.get("/feishu/chats", response_model=list[ChannelChatItem])
async def list_feishu_chats(
    user_id: Annotated[str, Depends(get_current_user_id)],
    agent_slug: str | None = None,
) -> list[ChannelChatItem]:
    """Groups this bot is already a member of, for the project-page picker.

    The bot must be added to the group in Feishu first — that is the half of
    the flow only an IM client can perform. ``agent_slug`` is optional: a
    Feishu app binds exactly one agent, so with a single configured bot the
    caller (a project page, which knows nothing about bots) need not name it.
    """
    from valuz_agent.integrations.feishu_long_connection import list_feishu_chats as fetch

    async with async_unit_of_work() as db:
        ds = AgentChannelBindingDatastore(db)
        binding = (
            await ds.get(user_id=user_id, platform=FEISHU_PLATFORM, agent_slug=agent_slug)
            if agent_slug
            else next(
                iter(await ds.list_enabled(platform=FEISHU_PLATFORM, user_id=user_id)),
                None,
            )
        )
        if binding is None:
            raise HTTPException(status_code=404, detail="feishu binding not found")
        secret = _read_feishu_secret(user_id=user_id, secret_ref=binding.secret_ref)
        bound = {
            row.external_chat_id: row.project_id
            for row in await ChannelChatBindingDatastore(db).list_all(user_id=user_id)
        }
    if not secret.app_secret:
        raise HTTPException(status_code=422, detail="App Secret is required")
    try:
        chats = await fetch(app_id=binding.bot_id, app_secret=secret.app_secret)
    except ChannelConfigError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return [
        ChannelChatItem(
            external_chat_id=chat_id,
            name=name,
            bound_project_id=bound.get(chat_id),
        )
        for chat_id, name in chats
    ]


class CreateChatRequestBody(BaseModel):
    name: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    channel_instance_id: str = Field(default="feishu-main", min_length=1)


class CreatedChatResponse(BaseModel):
    external_chat_id: str
    name: str
    project_id: str
    # How the human joins: the bot is the creator, so nobody else is in the
    # group yet. ``None`` when the link call failed — the group still exists.
    share_link: str | None = None


@router.post("/feishu/chats", response_model=CreatedChatResponse, status_code=201)
async def create_feishu_chat_for_project(
    body: CreateChatRequestBody,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> CreatedChatResponse:
    """Create a Feishu group with the bot in it and bind it to a project.

    Adding a bot to an existing group depends on a client menu that is absent
    or disabled in many setups; creating the group here avoids that path
    entirely.
    """
    from valuz_agent.integrations.feishu_long_connection import (
        create_feishu_chat as create_chat,
    )

    async with async_unit_of_work() as db:
        binding = next(
            iter(
                await AgentChannelBindingDatastore(db).list_enabled(
                    platform=FEISHU_PLATFORM, user_id=user_id
                )
            ),
            None,
        )
        if binding is None:
            raise HTTPException(status_code=404, detail="feishu binding not found")
        secret = _read_feishu_secret(user_id=user_id, secret_ref=binding.secret_ref)
    if not secret.app_secret:
        raise HTTPException(status_code=422, detail="App Secret is required")

    name = body.name.strip()
    try:
        chat_id, share_link = await create_chat(
            app_id=binding.bot_id, app_secret=secret.app_secret, name=name
        )
    except ChannelConfigError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    async with async_unit_of_work() as db:
        await ChannelChatBindingDatastore(db).upsert(
            user_id=user_id,
            channel_instance_id=body.channel_instance_id.strip(),
            external_chat_id=chat_id,
            project_id=body.project_id.strip(),
            external_chat_name=name,
        )
    return CreatedChatResponse(
        external_chat_id=chat_id,
        name=name,
        project_id=body.project_id.strip(),
        share_link=share_link,
    )


@router.get(
    "/chat-bindings",
    response_model=list[ChatProjectBindingResponse],
)
async def list_chat_bindings(
    user_id: Annotated[str, Depends(get_current_user_id)],
    project_id: str | None = None,
) -> list[ChatProjectBindingResponse]:
    async with async_unit_of_work() as db:
        ds = ChannelChatBindingDatastore(db)
        rows = (
            await ds.list_for_project(user_id=user_id, project_id=project_id)
            if project_id
            else await ds.list_all(user_id=user_id)
        )
    return [_chat_binding_response(row) for row in rows]


@router.put("/chat-bindings", response_model=ChatProjectBindingResponse)
async def bind_chat_to_project(
    body: ChatProjectBindingUpdate,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> ChatProjectBindingResponse:
    """Bind a chat to a project — "this group is that project".

    Rebinding overwrites: a chat holds exactly one project, otherwise the whole
    premise stops holding.
    """
    async with async_unit_of_work() as db:
        binding = await ChannelChatBindingDatastore(db).upsert(
            user_id=user_id,
            channel_instance_id=body.channel_instance_id.strip(),
            external_chat_id=body.external_chat_id.strip(),
            project_id=body.project_id.strip(),
            default_agent_slug=(body.default_agent_slug or "").strip() or None,
            external_chat_name=body.external_chat_name,
        )
    return _chat_binding_response(binding)


@router.delete("/chat-bindings", status_code=204)
async def unbind_chat(
    external_chat_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
    channel_instance_id: str = "feishu-main",
) -> None:
    async with async_unit_of_work() as db:
        removed = await ChannelChatBindingDatastore(db).delete(
            user_id=user_id,
            channel_instance_id=channel_instance_id,
            external_chat_id=external_chat_id,
        )
    if not removed:
        raise HTTPException(status_code=404, detail="chat binding not found")


def _chat_binding_response(binding: Any) -> ChatProjectBindingResponse:
    return ChatProjectBindingResponse(
        channel_instance_id=binding.channel_instance_id,
        external_chat_id=binding.external_chat_id,
        project_id=binding.project_id,
        external_chat_name=binding.external_chat_name,
        default_agent_slug=binding.default_agent_slug,
    )


def _wecom_aibot_binding_response(
    *,
    user_id: str,
    agent_slug: str,
    binding: AgentChannelBinding | None,
) -> WeComAIBotBindingResponse:
    runtime = wecom_aibot_supervisor.status_for(agent_slug)
    has_secret = bool(
        binding is not None
        and binding.secret_ref
        and secret_store.get(user_id, binding.secret_ref)
    )
    return WeComAIBotBindingResponse(
        enabled=binding.enabled if binding is not None else False,
        channel_instance_id=(
            binding.channel_instance_id if binding is not None else "wecom-aibot-main"
        ),
        owner_user_id=user_id,
        agent_slug=agent_slug,
        bot_id=binding.bot_id if binding is not None else "",
        has_secret=has_secret,
        connected=runtime.connected,
        connection_status=runtime.status,
        connection_error=runtime.last_error,
    )


def _wecom_aibot_secret_ref(agent_slug: str) -> str:
    return f"channel/wecom-aibot/{agent_slug}"


def _feishu_binding_response(
    *,
    user_id: str,
    agent_slug: str,
    binding: AgentChannelBinding | None,
) -> FeishuBindingResponse:
    runtime = feishu_supervisor.status_for(agent_slug)
    secret = (
        _read_feishu_secret(user_id=user_id, secret_ref=binding.secret_ref)
        if binding is not None
        else _FeishuSecretPayload()
    )
    return FeishuBindingResponse(
        enabled=binding.enabled if binding is not None else False,
        channel_instance_id=binding.channel_instance_id if binding is not None else "feishu-main",
        owner_user_id=user_id,
        agent_slug=agent_slug,
        app_id=binding.bot_id if binding is not None else "",
        has_app_secret=bool(secret.app_secret),
        has_verification_token=bool(secret.verification_token),
        has_encrypt_key=bool(secret.encrypt_key),
        connected=runtime.connected,
        connection_status=runtime.status,
        connection_error=runtime.last_error,
    )


def _feishu_secret_ref(agent_slug: str) -> str:
    return f"channel/feishu/{agent_slug}"


def _read_feishu_secret(
    *,
    user_id: str,
    secret_ref: str | None,
) -> _FeishuSecretPayload:
    if not secret_ref:
        return _FeishuSecretPayload()
    raw = secret_store.get(user_id, secret_ref)
    if not raw:
        return _FeishuSecretPayload()
    try:
        data = json.loads(raw)
    except JSONDecodeError:
        return _FeishuSecretPayload(verification_token=raw)
    if not isinstance(data, dict):
        return _FeishuSecretPayload()
    verification_token = data.get("verification_token")
    encrypt_key = data.get("encrypt_key")
    app_secret = data.get("app_secret")
    return _FeishuSecretPayload(
        app_secret=app_secret.strip()
        if isinstance(app_secret, str) and app_secret.strip()
        else None,
        verification_token=verification_token.strip()
        if isinstance(verification_token, str) and verification_token.strip()
        else None,
        encrypt_key=encrypt_key.strip()
        if isinstance(encrypt_key, str) and encrypt_key.strip()
        else None,
    )


def _write_feishu_secret(
    *,
    user_id: str,
    secret_ref: str,
    payload: _FeishuSecretPayload,
) -> None:
    secret_store.put(
        user_id,
        secret_ref,
        json.dumps(
            {
                "app_secret": payload.app_secret or "",
                "verification_token": payload.verification_token or "",
                "encrypt_key": payload.encrypt_key or "",
            },
            ensure_ascii=False,
        ),
    )


def _coalesce_secret_value(
    supplied: str | None,
    existing: str | None,
) -> str | None:
    stripped = supplied.strip() if supplied and supplied.strip() else None
    return stripped or existing


def _legacy_wecom_aibot_secret(agent_slug: str) -> str | None:
    try:
        config = load_wecom_aibot_config()
    except ChannelConfigError:
        return None
    if config.agent_slug != agent_slug:
        return None
    return config.secret


@router.get("/wecom/{channel_instance_id}/callback")
async def wecom_verify_url(
    channel_instance_id: str,
    request: Request,
) -> PlainTextResponse:
    try:
        adapter = WeComChannelAdapter(load_wecom_config(channel_instance_id))
        response = adapter.verify_url(query=dict(request.query_params))
    except ChannelConfigError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ChannelVerificationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return PlainTextResponse(response)


@router.post("/wecom/{channel_instance_id}/callback")
async def wecom_callback(
    channel_instance_id: str,
    request: Request,
    ingress: Annotated[ChannelIngressService, Depends(get_channel_ingress_service)],
) -> PlainTextResponse:
    try:
        config = load_wecom_config(channel_instance_id)
        adapter = WeComChannelAdapter(config)
        result = adapter.parse_callback(
            raw_body=await request.body(),
            query=dict(request.query_params),
        )
    except ChannelConfigError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ChannelVerificationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    if result is not None:
        await _dispatch_inbound(user_id=config.owner_user_id, ingress=ingress, inbound=result)
    return PlainTextResponse("success")


@router.post("/feishu/{channel_instance_id}/callback")
async def feishu_callback(
    channel_instance_id: str,
    request: Request,
    ingress: Annotated[ChannelIngressService, Depends(get_channel_ingress_service)],
) -> dict[str, Any]:
    try:
        owner_user_id, config = await _load_feishu_callback_config(channel_instance_id)
        adapter = FeishuChannelAdapter(config)
        result = adapter.parse_callback(
            raw_body=await request.body(),
            headers=dict(request.headers),
        )
    except ChannelConfigError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ChannelVerificationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    if isinstance(result, FeishuUrlVerificationResponse):
        return {"challenge": result.challenge}
    if result is not None:
        await _dispatch_inbound(user_id=owner_user_id, ingress=ingress, inbound=result)
    return {"code": 0}


async def _load_feishu_callback_config(
    channel_instance_id: str,
) -> tuple[str, FeishuChannelConfig]:
    async with async_unit_of_work() as db:
        binding = await AgentChannelBindingDatastore(db).get_enabled_by_channel_instance(
            platform=FEISHU_PLATFORM,
            channel_instance_id=channel_instance_id,
        )
    if binding is None:
        raise ChannelConfigError(
            f"Feishu channel instance '{channel_instance_id}' is not bound"
        )
    secret = _read_feishu_secret(
        user_id=binding.owner_user_id,
        secret_ref=binding.secret_ref,
    )
    if not secret.verification_token:
        raise ChannelConfigError("Feishu binding is missing verification token")
    return binding.owner_user_id, FeishuChannelConfig(
        channel_instance_id=binding.channel_instance_id,
        agent_slug=binding.agent_slug,
        verification_token=secret.verification_token,
        encrypt_key=secret.encrypt_key,
    )


async def _dispatch_inbound(
    *,
    user_id: str,
    ingress: ChannelIngressService,
    inbound: InboundChannelMessage,
) -> dict[str, Any]:
    result = await ingress.handle_inbound_message(user_id=user_id, inbound=inbound)
    return {
        "decision": result.decision.kind.value,
        "project_id": result.decision.project_id,
        "session_id": result.session_id,
        "candidates": [
            {
                "project_id": candidate.project_id,
                "project_name": candidate.project_name,
                "agent_slug": candidate.agent_slug,
            }
            for candidate in result.decision.candidates
        ],
    }
