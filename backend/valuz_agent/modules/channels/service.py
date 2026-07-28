"""Channel ingress orchestration."""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Any, Protocol

from valuz_agent.modules.channels.adapters import InboundChannelMessage
from valuz_agent.modules.channels.commands import (
    ChannelCommandKind,
    parse_channel_command,
)
from valuz_agent.modules.channels.resolver import (
    CHAT_PROJECT_SENTINEL,
    AgentChannelResolver,
)
from valuz_agent.modules.channels.schemas import (
    AgentChannelRouteDecision,
    AgentPlacement,
    ChannelRouteDecisionKind,
    ChannelRouteKey,
    ChannelThreadBinding,
)

logger = logging.getLogger(__name__)

CHANNEL_BOUND_MESSAGE = "已绑定到项目「{project}」，之后这个群里的对话都会进入该项目。"
CHANNEL_UNBOUND_MESSAGE = "已解除项目绑定。"
CHANNEL_NO_PROJECT_BOUND_MESSAGE = "这个会话还没有绑定项目。"
CHANNEL_CURRENT_PROJECT_MESSAGE = "当前绑定的项目是「{project}」。"
CHANNEL_PROJECT_NOT_FOUND_MESSAGE = "没有找到项目「{name}」。可选项目：{candidates}"

_DIRECT_TURN_SESSION_STATUSES = {"created", "idle"}


class AgentPlacementReader(Protocol):
    async def list_placements(
        self,
        user_id: str,
        source_agent_slug: str,
    ) -> list[AgentPlacement]: ...


class ChannelChatBindingStore(Protocol):
    async def get(
        self,
        *,
        user_id: str,
        channel_instance_id: str,
        external_chat_id: str,
    ) -> Any: ...

    async def upsert(
        self,
        *,
        user_id: str,
        channel_instance_id: str,
        external_chat_id: str,
        project_id: str,
        default_agent_slug: str | None = None,
        external_chat_name: str | None = None,
        bound_by_external_user: str | None = None,
    ) -> Any: ...

    async def delete(
        self, *, user_id: str, channel_instance_id: str, external_chat_id: str
    ) -> bool: ...


class ChannelThreadBindingStore(Protocol):
    async def get_for_thread(
        self,
        *,
        user_id: str,
        channel_instance_id: str,
        external_chat_id: str,
        external_thread_id: str,
        agent_slug: str,
    ) -> ChannelThreadBinding | None: ...

    async def upsert(self, *, user_id: str, key: ChannelRouteKey, session_id: str) -> None: ...


class ChannelSessionRef(Protocol):
    id: str
    # The project the session actually landed in. Differs from the requested id
    # when the quick-chat sentinel is expanded into a fresh chat project.
    project_id: str


class ChannelSessionRunner(Protocol):
    async def create_session(
        self,
        *,
        user_id: str,
        project_id: str,
        agent_slug: str,
        origin: str,
        creation_context: dict[str, str],
    ) -> ChannelSessionRef: ...

    async def send_message(self, *, user_id: str, session_id: str, content: str) -> None: ...

    async def get_session_status(self, *, user_id: str, session_id: str) -> str | None: ...

    async def enqueue_message(self, *, user_id: str, session_id: str, content: str) -> None: ...


@dataclass(frozen=True, slots=True)
class ChannelIngressResult:
    decision: AgentChannelRouteDecision
    session_id: str | None = None
    # Set when the turn was a binding command rather than work for an agent:
    # the runner posts this verbatim and starts no session.
    direct_reply: str | None = None


class ChannelIngressService:
    def __init__(
        self,
        *,
        placements: AgentPlacementReader,
        bindings: ChannelThreadBindingStore,
        sessions: ChannelSessionRunner,
        resolver: AgentChannelResolver | None = None,
        chat_bindings: ChannelChatBindingStore | None = None,
    ) -> None:
        self._placements = placements
        self._bindings = bindings
        self._sessions = sessions
        self._resolver = resolver or AgentChannelResolver()
        self._chat_bindings = chat_bindings

    async def handle_inbound_message(
        self,
        *,
        user_id: str,
        inbound: InboundChannelMessage,
    ) -> ChannelIngressResult:
        context = inbound.context
        command_result = await self._handle_command(user_id=user_id, inbound=inbound)
        if command_result is not None:
            return command_result
        placements = await self._placements.list_placements(
            user_id,
            context.mentioned_agent_slug,
        )
        thread_id = context.external_thread_id or context.external_chat_id
        existing_binding = await self._bindings.get_for_thread(
            user_id=user_id,
            channel_instance_id=context.channel_instance_id,
            external_chat_id=context.external_chat_id,
            external_thread_id=thread_id,
            agent_slug=context.mentioned_agent_slug,
        )
        existing_binding = await self._binding_with_live_session_status(
            user_id=user_id,
            binding=existing_binding,
        )
        decision = self._resolver.resolve(
            context,
            placements=placements,
            existing_binding=existing_binding,
            chat_project_id=await self._chat_project_id(user_id=user_id, context=context),
        )
        if decision.kind == ChannelRouteDecisionKind.QUEUE_SESSION and decision.session_id:
            await self._sessions.enqueue_message(
                user_id=user_id,
                session_id=decision.session_id,
                content=inbound.text,
            )
            return ChannelIngressResult(decision=decision, session_id=decision.session_id)

        if decision.kind == ChannelRouteDecisionKind.REUSE_SESSION and decision.session_id:
            await self._sessions.send_message(
                user_id=user_id,
                session_id=decision.session_id,
                content=inbound.text,
            )
            return ChannelIngressResult(decision=decision, session_id=decision.session_id)

        if decision.kind != ChannelRouteDecisionKind.NEW_SESSION or decision.project_id is None:
            return ChannelIngressResult(decision=decision)

        # No placement for the quick-chat sentinel — the agent is used straight
        # from the library, exactly like a project-less conversation in the app.
        agent_slug = (
            context.mentioned_agent_slug
            if decision.project_id == CHAT_PROJECT_SENTINEL
            else _placement_for_project(placements, decision.project_id).agent_slug
        )
        created = await self._sessions.create_session(
            user_id=user_id,
            project_id=decision.project_id,
            agent_slug=agent_slug,
            origin="channel",
            creation_context={
                "kind": "channel",
                "channel_instance_id": context.channel_instance_id,
                "external_chat_id": context.external_chat_id,
                "external_thread_id": thread_id,
                "request_id": context.request_id or "",
            },
        )
        session_id = str(created.id)
        await self._sessions.send_message(
            user_id=user_id,
            session_id=session_id,
            content=inbound.text,
        )
        # Bind the project the session actually landed in: the sentinel is
        # expanded into a fresh chat project, and storing the sentinel would
        # make every follow-up open a brand-new chat.
        bound_project_id = getattr(created, "project_id", None) or decision.project_id
        key = self._resolver.route_key(context, project_id=str(bound_project_id))
        await self._bindings.upsert(user_id=user_id, key=key, session_id=session_id)
        return ChannelIngressResult(decision=decision, session_id=session_id)

    async def _handle_command(
        self,
        *,
        user_id: str,
        inbound: InboundChannelMessage,
    ) -> ChannelIngressResult | None:
        """Bind / show / unbind the chat's project, in the chat itself.

        Returns ``None`` for ordinary messages so they route to an agent as
        usual. Commands never reach the agent — they are configuration, and
        answering them with a model would make the outcome non-deterministic.
        """
        command = parse_channel_command(inbound.text)
        if command is None or self._chat_bindings is None:
            return None
        context = inbound.context
        keys = {
            "user_id": user_id,
            "channel_instance_id": context.channel_instance_id,
            "external_chat_id": context.external_chat_id,
        }

        if command.kind is ChannelCommandKind.SHOW_PROJECT:
            binding = await self._chat_bindings.get(**keys)
            project_id = getattr(binding, "project_id", None)
            name = await self._project_display_name(user_id, project_id)
            return self._command_result(
                CHANNEL_CURRENT_PROJECT_MESSAGE.format(project=name)
                if name
                else CHANNEL_NO_PROJECT_BOUND_MESSAGE
            )

        if command.kind is ChannelCommandKind.UNBIND_PROJECT:
            removed = await self._chat_bindings.delete(**keys)
            return self._command_result(
                CHANNEL_UNBOUND_MESSAGE if removed else CHANNEL_NO_PROJECT_BOUND_MESSAGE
            )

        placements = await self._placements.list_placements(
            user_id, context.mentioned_agent_slug
        )
        target = _match_project_by_name(placements, command.argument or "")
        if target is None:
            names = "、".join(p.project_name or p.project_id for p in placements)
            return self._command_result(
                CHANNEL_PROJECT_NOT_FOUND_MESSAGE.format(
                    name=command.argument or "", candidates=names or "（无）"
                )
            )
        await self._chat_bindings.upsert(
            project_id=target.project_id,
            bound_by_external_user=context.external_user_id,
            **keys,
        )
        return self._command_result(
            CHANNEL_BOUND_MESSAGE.format(project=target.project_name or target.project_id)
        )

    @staticmethod
    def _command_result(message: str) -> ChannelIngressResult:
        return ChannelIngressResult(
            decision=AgentChannelRouteDecision(
                kind=ChannelRouteDecisionKind.NEW_SESSION,
                agent_slug="",
                project_id=None,
                session_id=None,
                reason="channel_command",
            ),
            direct_reply=message,
        )

    async def _project_display_name(self, user_id: str, project_id: str | None) -> str | None:
        if not project_id:
            return None
        from valuz_agent.modules.projects.service import project_name_map

        try:
            return (await project_name_map(user_id)).get(project_id, project_id)
        except Exception:  # noqa: BLE001 - a name lookup must not break the reply
            logger.warning("Failed to resolve project name for %s", project_id, exc_info=True)
            return project_id

    async def _chat_project_id(
        self,
        *,
        user_id: str,
        context: Any,
    ) -> str | None:
        """The project this chat is bound to, if any ("this group is that
        project"). A read failure degrades to the placement heuristics rather
        than dropping the turn."""
        if self._chat_bindings is None:
            return None
        try:
            binding = await self._chat_bindings.get(
                user_id=user_id,
                channel_instance_id=context.channel_instance_id,
                external_chat_id=context.external_chat_id,
            )
        except Exception:  # noqa: BLE001 - routing must survive a binding read
            logger.warning(
                "Failed to read the chat project binding: channel=%s chat=%s",
                context.channel_instance_id,
                context.external_chat_id,
                exc_info=True,
            )
            return None
        return getattr(binding, "project_id", None) if binding is not None else None

    async def _binding_with_live_session_status(
        self,
        *,
        user_id: str,
        binding: ChannelThreadBinding | None,
    ) -> ChannelThreadBinding | None:
        if binding is None or not binding.session_id:
            return binding
        try:
            status = await self._sessions.get_session_status(
                user_id=user_id,
                session_id=binding.session_id,
            )
        except Exception:  # noqa: BLE001 - stale bindings should not drop the channel turn
            logger.warning(
                "Failed to read bound channel session status: channel=%s chat=%s session=%s",
                binding.channel_instance_id,
                binding.external_chat_id,
                binding.session_id,
                exc_info=True,
            )
            return replace(
                binding,
                session_accepts_turn=False,
                session_status="missing",
            )
        return replace(
            binding,
            session_accepts_turn=status in _DIRECT_TURN_SESSION_STATUSES,
            session_status=status,
        )


def _match_project_by_name(placements: list[AgentPlacement], name: str) -> AgentPlacement | None:
    """Match a project by name, normalized like the resolver's own hint match."""
    wanted = " ".join(name.strip().casefold().split())
    if not wanted:
        return None
    for placement in placements:
        candidate = " ".join((placement.project_name or "").strip().casefold().split())
        if candidate and candidate == wanted:
            return placement
    return None


def _placement_for_project(placements: list[AgentPlacement], project_id: str) -> AgentPlacement:
    for placement in placements:
        if placement.project_id == project_id:
            return placement
    raise ValueError(f"agent placement for project '{project_id}' not found")


__all__ = [
    "AgentPlacementReader",
    "ChannelChatBindingStore",
    "ChannelIngressResult",
    "ChannelIngressService",
    "ChannelSessionRef",
    "ChannelSessionRunner",
    "ChannelThreadBindingStore",
]
