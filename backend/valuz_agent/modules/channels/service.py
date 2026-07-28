"""Channel ingress orchestration."""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Any, Protocol

from valuz_agent.modules.channels.adapters import InboundChannelMessage
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
_DIRECT_TURN_SESSION_STATUSES = {"created", "idle"}


class AgentPlacementReader(Protocol):
    async def list_placements(
        self,
        user_id: str,
        source_agent_slug: str,
    ) -> list[AgentPlacement]: ...


class ChannelChatBindingReader(Protocol):
    async def get(
        self,
        *,
        user_id: str,
        channel_instance_id: str,
        external_chat_id: str,
    ) -> Any: ...


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


class ChannelIngressService:
    def __init__(
        self,
        *,
        placements: AgentPlacementReader,
        bindings: ChannelThreadBindingStore,
        sessions: ChannelSessionRunner,
        resolver: AgentChannelResolver | None = None,
        chat_bindings: ChannelChatBindingReader | None = None,
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


def _placement_for_project(placements: list[AgentPlacement], project_id: str) -> AgentPlacement:
    for placement in placements:
        if placement.project_id == project_id:
            return placement
    raise ValueError(f"agent placement for project '{project_id}' not found")


__all__ = [
    "AgentPlacementReader",
    "ChannelChatBindingReader",
    "ChannelIngressResult",
    "ChannelIngressService",
    "ChannelSessionRef",
    "ChannelSessionRunner",
    "ChannelThreadBindingStore",
]
