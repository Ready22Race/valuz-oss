"""Feishu long-connection runtime."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any, Protocol, cast

from valuz_agent.infra import secret_store
from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.channels.adapters import (
    FeishuChannelAdapter,
    FeishuChannelConfig,
    InboundChannelMessage,
)
from valuz_agent.modules.channels.config import ChannelConfigError
from valuz_agent.modules.channels.datastore import AgentChannelBindingDatastore
from valuz_agent.modules.channels.schemas import ChannelRouteDecisionKind
from valuz_agent.modules.channels.service import ChannelIngressResult

logger = logging.getLogger(__name__)

CHANNEL_EXECUTION_ERROR_MESSAGE = "执行异常，任务没有成功提交，请稍后重试或联系管理员。"
CHANNEL_NO_ROUTE_MESSAGE = "消息已收到，但没有找到可执行的项目绑定。"
CHANNEL_RECEIVED_MESSAGE = "收到，正在处理。"
CHANNEL_QUEUED_MESSAGE = "已加入队列，当前任务结束后会继续处理。"
CHANNEL_EMPTY_RESULT_MESSAGE = "执行完成，但没有返回文本结果。"


@dataclass(frozen=True, slots=True)
class FeishuLongConnectionConfig:
    channel_instance_id: str
    owner_user_id: str
    agent_slug: str
    app_id: str
    app_secret: str
    verification_token: str | None = None
    encrypt_key: str | None = None


@dataclass(frozen=True, slots=True)
class FeishuRuntimeStatus:
    status: str
    connected: bool = False
    last_error: str | None = None


class FeishuWsClient(Protocol):
    on_reconnecting: Callable[[], None]
    on_reconnected: Callable[[], None]

    async def _connect(self) -> None: ...

    async def _disconnect(self) -> None: ...

    async def _ping_loop(self) -> None: ...


ClientFactory = Callable[[FeishuLongConnectionConfig, Any], FeishuWsClient]
DispatchInbound = Callable[[InboundChannelMessage], Awaitable[ChannelIngressResult | None]]
ReplySender = Callable[
    [FeishuLongConnectionConfig, InboundChannelMessage, str],
    Awaitable[str | None],
]
ReplyUpdater = Callable[[FeishuLongConnectionConfig, str, str], Awaitable[None]]
AuthenticatedCallback = Callable[[], None]
ReconnectingCallback = Callable[[], None]
SessionEventStreamFactory = Callable[[str, str], AsyncIterator[Any]]


class FeishuLongConnectionRunner:
    def __init__(
        self,
        config: FeishuLongConnectionConfig,
        *,
        dispatch: DispatchInbound,
        client_factory: ClientFactory | None = None,
        reply_sender: ReplySender | None = None,
        reply_updater: ReplyUpdater | None = None,
        on_authenticated: AuthenticatedCallback | None = None,
        on_reconnecting: ReconnectingCallback | None = None,
        session_event_stream_factory: SessionEventStreamFactory | None = None,
    ) -> None:
        self._config = config
        self._dispatch = dispatch
        self._client_factory = client_factory or _new_sdk_client
        self._reply_sender = reply_sender or _send_feishu_text_reply
        self._reply_updater = reply_updater or _patch_feishu_text_message
        self._on_authenticated = on_authenticated
        self._on_reconnecting = on_reconnecting
        self._session_event_stream_factory = (
            session_event_stream_factory or _subscribe_session_events
        )
        self._loop: asyncio.AbstractEventLoop | None = None
        self._dispatch_tasks: set[asyncio.Task[None]] = set()

    async def run_once(self, stop_event: asyncio.Event) -> None:
        self._loop = asyncio.get_running_loop()
        event_handler = _build_event_handler(self._config, self._handle_event)
        client = self._client_factory(self._config, event_handler)
        client.on_reconnecting = self._handle_reconnecting
        client.on_reconnected = self._handle_reconnected
        ping_task: asyncio.Task[None] | None = None
        try:
            await client._connect()
            self._handle_reconnected()
            ping_task = asyncio.create_task(client._ping_loop(), name="feishu-ping")
            await stop_event.wait()
        finally:
            if ping_task is not None:
                ping_task.cancel()
                await _await_cancelled(ping_task)
            await client._disconnect()
            for task in list(self._dispatch_tasks):
                task.cancel()
                await _await_cancelled(task)
            self._dispatch_tasks.clear()

    def _handle_event(self, event: Any) -> None:
        try:
            inbound = inbound_from_sdk_event(event, self._config)
        except Exception:
            logger.exception(
                "Feishu event parse failed: channel=%s agent=%s",
                self._config.channel_instance_id,
                self._config.agent_slug,
            )
            return

        loop = self._loop
        if loop is None:
            logger.warning("Feishu event received before runner loop was ready")
            return
        task = loop.create_task(self._dispatch_event(inbound), name="feishu-dispatch")
        self._dispatch_tasks.add(task)
        task.add_done_callback(self._dispatch_tasks.discard)

    async def _dispatch_event(self, inbound: InboundChannelMessage) -> None:
        reply_message_id = await self._try_send_channel_reply(
            inbound,
            CHANNEL_RECEIVED_MESSAGE,
        )
        try:
            result = await self._dispatch(inbound)
        except Exception:
            logger.exception(
                "Feishu inbound dispatch failed: channel=%s agent=%s msg=%s",
                self._config.channel_instance_id,
                self._config.agent_slug,
                inbound.context.external_message_id,
            )
            await self._patch_or_send_channel_reply(
                inbound,
                reply_message_id,
                CHANNEL_EXECUTION_ERROR_MESSAGE,
            )
            return
        logger.info(
            "Feishu routed message: decision=%s session=%s",
            result.decision.kind.value if result is not None else "none",
            result.session_id if result is not None else None,
        )
        await self._stream_dispatch_result(inbound, result, reply_message_id)

    async def _try_send_channel_reply(
        self,
        inbound: InboundChannelMessage,
        content: str,
    ) -> str | None:
        try:
            return await self._reply_sender(self._config, inbound, content)
        except ChannelConfigError as exc:
            logger.warning("Feishu reply was not accepted: %s", exc)
        except Exception as exc:  # noqa: BLE001 - channel replies are best-effort
            logger.warning("Feishu reply failed: %s", exc, exc_info=True)
        return None

    async def _try_patch_channel_reply(self, message_id: str, content: str) -> bool:
        try:
            await self._reply_updater(self._config, message_id, content)
        except ChannelConfigError as exc:
            logger.warning("Feishu reply update was not accepted: %s", exc)
            return False
        except Exception as exc:  # noqa: BLE001 - channel replies are best-effort
            logger.warning("Feishu reply update failed: %s", exc, exc_info=True)
            return False
        return True

    async def _patch_or_send_channel_reply(
        self,
        inbound: InboundChannelMessage,
        reply_message_id: str | None,
        content: str,
    ) -> str | None:
        if reply_message_id and await self._try_patch_channel_reply(reply_message_id, content):
            return reply_message_id
        return await self._try_send_channel_reply(inbound, content)

    async def _stream_dispatch_result(
        self,
        inbound: InboundChannelMessage,
        result: ChannelIngressResult | None,
        reply_message_id: str | None,
    ) -> None:
        if result is not None and result.decision.kind == ChannelRouteDecisionKind.QUEUE_SESSION:
            await self._patch_or_send_channel_reply(
                inbound,
                reply_message_id,
                CHANNEL_QUEUED_MESSAGE,
            )
            return

        session_id = result.session_id if result is not None else None
        if not session_id:
            await self._patch_or_send_channel_reply(
                inbound,
                reply_message_id,
                _route_feedback_message(result),
            )
            return

        user_id = inbound.context.user_id
        accumulated = ""
        last_sent = ""
        logger.info(
            "Feishu streaming session output: channel=%s agent=%s session=%s",
            self._config.channel_instance_id,
            self._config.agent_slug,
            session_id,
        )
        try:
            async for event in self._session_event_stream_factory(user_id, session_id):
                event_type, data = _event_type_and_data(event)
                logger.debug(
                    "Feishu session event: channel=%s agent=%s session=%s type=%s",
                    self._config.channel_instance_id,
                    self._config.agent_slug,
                    session_id,
                    event_type,
                )
                if event_type == "text_delta":
                    text = _event_text(data)
                    if not text:
                        continue
                    accumulated += text
                    if accumulated != last_sent and reply_message_id:
                        if await self._try_patch_channel_reply(reply_message_id, accumulated):
                            last_sent = accumulated
                        else:
                            reply_message_id = None
                    continue
                if event_type == "assistant_message":
                    text = _event_text(data)
                    if not text:
                        continue
                    accumulated = text
                    if accumulated != last_sent and reply_message_id:
                        if await self._try_patch_channel_reply(reply_message_id, accumulated):
                            last_sent = accumulated
                        else:
                            reply_message_id = None
                    continue
                if event_type == "session_error":
                    logger.warning(
                        "Feishu observed session error: channel=%s agent=%s session=%s",
                        self._config.channel_instance_id,
                        self._config.agent_slug,
                        session_id,
                    )
                    await self._patch_or_send_channel_reply(
                        inbound,
                        reply_message_id,
                        CHANNEL_EXECUTION_ERROR_MESSAGE,
                    )
                    return
                if _is_terminal_session_event(event_type, data):
                    await self._patch_or_send_channel_reply(
                        inbound,
                        reply_message_id,
                        accumulated.strip() or CHANNEL_EMPTY_RESULT_MESSAGE,
                    )
                    return
        except Exception:
            logger.exception(
                "Feishu session event stream failed: channel=%s agent=%s session=%s",
                self._config.channel_instance_id,
                self._config.agent_slug,
                session_id,
            )
            await self._patch_or_send_channel_reply(
                inbound,
                reply_message_id,
                CHANNEL_EXECUTION_ERROR_MESSAGE,
            )
            return

        if accumulated:
            await self._patch_or_send_channel_reply(inbound, reply_message_id, accumulated)

    def _handle_reconnecting(self) -> None:
        self._on_reconnecting and self._on_reconnecting()

    def _handle_reconnected(self) -> None:
        self._on_authenticated and self._on_authenticated()


class FeishuSupervisor:
    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._stop_events: dict[str, asyncio.Event] = {}
        self._statuses: dict[str, FeishuRuntimeStatus] = {}
        self._startup_task: asyncio.Task[None] | None = None

    def status_for(self, agent_slug: str) -> FeishuRuntimeStatus:
        return self._statuses.get(agent_slug, FeishuRuntimeStatus(status="stopped"))

    async def startup(self) -> None:
        if self._startup_task is not None and not self._startup_task.done():
            return
        self._startup_task = asyncio.create_task(
            self._startup_connect(),
            name="feishu-startup-connect",
        )

    async def restart(self) -> None:
        await self._cancel_startup_task()
        await self._shutdown_connections()
        configs = await _load_enabled_feishu_configs()
        for config in configs:
            stop_event = asyncio.Event()
            self._stop_events[config.agent_slug] = stop_event
            self._statuses[config.agent_slug] = FeishuRuntimeStatus(status="connecting")
            runner = FeishuLongConnectionRunner(
                config,
                dispatch=_dispatch_to_channel_ingress,
                on_authenticated=self._mark_connected_callback(config.agent_slug),
                on_reconnecting=self._mark_connecting_callback(config.agent_slug),
            )
            self._tasks[config.agent_slug] = asyncio.create_task(
                self._run_loop(config.agent_slug, runner, stop_event),
                name=f"feishu-{config.agent_slug}",
            )

    async def shutdown(self) -> None:
        await self._cancel_startup_task()
        await self._shutdown_connections()

    async def _startup_connect(self) -> None:
        try:
            await asyncio.sleep(0)
            await self.restart()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - channels must never break app startup
            self._statuses["startup"] = FeishuRuntimeStatus(
                status="error",
                connected=False,
                last_error=str(exc),
            )
            logger.warning("Feishu startup connection failed: %s", exc, exc_info=True)
        finally:
            if self._startup_task is asyncio.current_task():
                self._startup_task = None

    async def _shutdown_connections(self) -> None:
        for stop_event in self._stop_events.values():
            stop_event.set()
        for task in self._tasks.values():
            task.cancel()
            await _await_cancelled(task)
        self._tasks.clear()
        self._stop_events.clear()
        self._statuses.clear()

    async def _cancel_startup_task(self) -> None:
        task = self._startup_task
        if task is None:
            return
        if task is asyncio.current_task():
            return
        self._startup_task = None
        if not task.done():
            task.cancel()
            await _await_cancelled(task)

    async def _run_loop(
        self,
        agent_slug: str,
        runner: FeishuLongConnectionRunner,
        stop_event: asyncio.Event,
    ) -> None:
        backoff_s = 1.0
        while not stop_event.is_set():
            try:
                self._statuses[agent_slug] = FeishuRuntimeStatus(status="connecting")
                await runner.run_once(stop_event)
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - background runner must survive outages
                self._statuses[agent_slug] = FeishuRuntimeStatus(
                    status="error",
                    connected=False,
                    last_error=str(exc),
                )
                logger.warning("Feishu connection failed: %s", exc)
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=backoff_s)
                except TimeoutError:
                    backoff_s = min(backoff_s * 2, 30.0)

    def _mark_connected(self, agent_slug: str) -> None:
        self._statuses[agent_slug] = FeishuRuntimeStatus(status="connected", connected=True)

    def _mark_connecting(self, agent_slug: str) -> None:
        self._statuses[agent_slug] = FeishuRuntimeStatus(status="connecting")

    def _mark_connected_callback(self, agent_slug: str) -> AuthenticatedCallback:
        def callback() -> None:
            self._mark_connected(agent_slug)

        return callback

    def _mark_connecting_callback(self, agent_slug: str) -> ReconnectingCallback:
        def callback() -> None:
            self._mark_connecting(agent_slug)

        return callback


def inbound_from_sdk_event(
    event: Any,
    config: FeishuLongConnectionConfig,
) -> InboundChannelMessage:
    from lark_oapi.core.json import JSON  # type: ignore[import-untyped]

    raw_body = JSON.marshal(event).encode("utf-8")
    parsed = FeishuChannelAdapter(
        FeishuChannelConfig(
            channel_instance_id=config.channel_instance_id,
            agent_slug=config.agent_slug,
            verification_token=config.verification_token,
            encrypt_key=None,
        )
    ).parse_callback(raw_body=raw_body, headers={})
    if not isinstance(parsed, InboundChannelMessage):
        raise ValueError("Feishu SDK event did not produce an inbound message")
    return replace(
        parsed,
        context=replace(parsed.context, user_id=config.owner_user_id),
    )


async def _dispatch_to_channel_ingress(
    inbound: InboundChannelMessage,
) -> ChannelIngressResult | None:
    from valuz_agent.api.deps import get_channel_ingress_service

    user_id = inbound.context.user_id
    if not user_id:
        logger.warning("Feishu inbound missing owner user id; message ignored")
        return None
    service_gen = get_channel_ingress_service()
    service = await service_gen.__anext__()
    try:
        return await service.handle_inbound_message(user_id=user_id, inbound=inbound)
    finally:
        try:
            await service_gen.__anext__()
        except StopAsyncIteration:
            pass


async def _load_enabled_feishu_configs() -> list[FeishuLongConnectionConfig]:
    from valuz_agent.infra.local_identity import resolve_local_user_id

    owner = resolve_local_user_id()
    async with async_unit_of_work() as db:
        rows = await AgentChannelBindingDatastore(db).list_enabled(
            user_id=owner,
            platform="feishu",
        )
    configs: list[FeishuLongConnectionConfig] = []
    for row in rows:
        secret = _read_secret(owner, row.secret_ref)
        if not secret.get("app_secret"):
            logger.warning("Feishu binding for %s has no stored app secret", row.agent_slug)
            continue
        configs.append(
            FeishuLongConnectionConfig(
                channel_instance_id=row.channel_instance_id,
                owner_user_id=owner,
                agent_slug=row.agent_slug,
                app_id=row.bot_id,
                app_secret=str(secret["app_secret"]),
                verification_token=_optional_str(secret.get("verification_token")),
                encrypt_key=_optional_str(secret.get("encrypt_key")),
            )
        )
    return configs


def _read_secret(user_id: str, secret_ref: str | None) -> dict[str, Any]:
    if not secret_ref:
        return {}
    raw = secret_store.get(user_id, secret_ref)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"verification_token": raw}
    return data if isinstance(data, dict) else {}


def _optional_str(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _build_event_handler(
    config: FeishuLongConnectionConfig,
    callback: Callable[[Any], None],
) -> Any:
    from lark_oapi.event.dispatcher_handler import (  # type: ignore[import-untyped]
        EventDispatcherHandler,
    )

    return (
        EventDispatcherHandler.builder(
            config.encrypt_key or "",
            config.verification_token or "",
        )
        .register_p2_im_chat_access_event_bot_p2p_chat_entered_v1(_ignore_feishu_event)
        .register_p2_im_message_receive_v1(callback)
        .build()
    )


async def _send_feishu_text_reply(
    config: FeishuLongConnectionConfig,
    inbound: InboundChannelMessage,
    content: str,
) -> str | None:
    from lark_oapi.api.im.v1 import (  # type: ignore[import-untyped]
        ReplyMessageRequest,
        ReplyMessageRequestBody,
    )

    source_message_id = inbound.context.external_message_id
    if not source_message_id:
        raise ChannelConfigError("Feishu cannot reply without source message id")
    client = _new_openapi_client(config)
    body = (
        ReplyMessageRequestBody.builder()
        .msg_type("text")
        .content(_feishu_text_content(content))
        .reply_in_thread(True)
        .build()
    )
    request = (
        ReplyMessageRequest.builder()
        .message_id(source_message_id)
        .request_body(body)
        .build()
    )
    response = await client.im.v1.message.areply(request)
    if not response.success():
        raise ChannelConfigError(
            f"Feishu reply failed: {response.code} {response.msg or ''}".strip()
        )
    return response.data.message_id if response.data is not None else None


async def _patch_feishu_text_message(
    config: FeishuLongConnectionConfig,
    message_id: str,
    content: str,
) -> None:
    from lark_oapi.api.im.v1 import (
        PatchMessageRequest,
        PatchMessageRequestBody,
    )

    client = _new_openapi_client(config)
    body = PatchMessageRequestBody.builder().content(_feishu_text_content(content)).build()
    request = (
        PatchMessageRequest.builder()
        .message_id(message_id)
        .request_body(body)
        .build()
    )
    response = await client.im.v1.message.apatch(request)
    if not response.success():
        raise ChannelConfigError(
            f"Feishu reply update failed: {response.code} {response.msg or ''}".strip()
        )


async def _subscribe_session_events(user_id: str, session_id: str) -> AsyncIterator[Any]:
    from valuz_agent.adapters import kernel_client

    async for event in kernel_client.subscribe_session_events(user_id, session_id):
        yield event


def _new_openapi_client(config: FeishuLongConnectionConfig) -> Any:
    import lark_oapi as lark  # type: ignore[import-untyped]
    from lark_oapi.core.enum import LogLevel  # type: ignore[import-untyped]

    return (
        lark.Client.builder()
        .app_id(config.app_id)
        .app_secret(config.app_secret)
        .log_level(LogLevel.INFO)
        .build()
    )


def _feishu_text_content(content: str) -> str:
    return json.dumps({"text": content}, ensure_ascii=False)


def _event_type_and_data(event: Any) -> tuple[str, dict[str, Any]]:
    if isinstance(event, dict):
        event_type = str(event.get("type") or "")
        data = event.get("data")
    else:
        event_type = str(getattr(event, "type", "") or "")
        data = getattr(event, "data", None)
    return event_type, data if isinstance(data, dict) else {}


def _event_text(data: dict[str, Any]) -> str:
    text = data.get("text")
    if text is None:
        text = data.get("content")
    if text is None:
        text = data.get("delta")
    return str(text or "")


def _is_terminal_session_event(event_type: str, data: dict[str, Any]) -> bool:
    if event_type == "session_idle":
        return True
    if event_type != "session_update":
        return False
    status = data.get("status")
    return status in {"idle", "terminated"}


def _route_feedback_message(result: ChannelIngressResult | None) -> str:
    if result is None:
        return CHANNEL_NO_ROUTE_MESSAGE
    decision = result.decision
    if decision.kind == ChannelRouteDecisionKind.ASK_PROJECT:
        candidate_names = [
            candidate.project_name or candidate.project_id for candidate in decision.candidates
        ]
        if candidate_names:
            return "这个 Agent 派驻了多个项目，请在消息里说明项目名后再试。可选项目：" + "、".join(
                candidate_names
            )
        return "这个 Agent 派驻了多个项目，请在消息里说明项目名后再试。"
    if decision.kind == ChannelRouteDecisionKind.NOT_DEPLOYED:
        return "这个 Agent 还没有派驻到项目，暂时无法执行。"
    return CHANNEL_NO_ROUTE_MESSAGE


def _ignore_feishu_event(event: Any) -> None:
    logger.info("Feishu event ignored: %s", type(event).__name__)


def _new_sdk_client(config: FeishuLongConnectionConfig, event_handler: Any) -> FeishuWsClient:
    import lark_oapi as lark
    from lark_oapi.core.enum import LogLevel
    from lark_oapi.ws import client as ws_client_module  # type: ignore[import-untyped]

    # The SDK stores its event loop in a module global. Keep it aligned with the
    # FastAPI loop so its private async connection API can be managed by us.
    ws_client_module.loop = asyncio.get_running_loop()
    return cast(
        FeishuWsClient,
        lark.ws.Client(
            config.app_id,
            config.app_secret,
            log_level=LogLevel.INFO,
            event_handler=event_handler,
            auto_reconnect=True,
        ),
    )


async def _await_cancelled(task: asyncio.Task[None]) -> None:
    try:
        await task
    except asyncio.CancelledError:
        pass


feishu_supervisor = FeishuSupervisor()


__all__ = [
    "FeishuLongConnectionConfig",
    "FeishuLongConnectionRunner",
    "FeishuRuntimeStatus",
    "FeishuSupervisor",
    "feishu_supervisor",
    "inbound_from_sdk_event",
]
