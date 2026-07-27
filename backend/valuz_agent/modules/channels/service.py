"""Channel ingress orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from valuz_agent.modules.channels.adapters import InboundChannelMessage
from valuz_agent.modules.channels.resolver import AgentChannelResolver
from valuz_agent.modules.channels.schemas import (
    AgentChannelRouteDecision,
    AgentPlacement,
    ChannelRouteDecisionKind,
    ChannelRouteKey,
    ChannelThreadBinding,
)


class AgentPlacementReader(Protocol):
    async def list_placements(
        self,
        user_id: str,
        source_agent_slug: str,
    ) -> list[AgentPlacement]: ...


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
    ) -> None:
        self._placements = placements
        self._bindings = bindings
        self._sessions = sessions
        self._resolver = resolver or AgentChannelResolver()

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
        decision = self._resolver.resolve(
            context,
            placements=placements,
            existing_binding=existing_binding,
        )
        if decision.kind == ChannelRouteDecisionKind.REUSE_SESSION and decision.session_id:
            await self._sessions.send_message(
                user_id=user_id,
                session_id=decision.session_id,
                content=inbound.text,
            )
            return ChannelIngressResult(decision=decision, session_id=decision.session_id)

        if decision.kind != ChannelRouteDecisionKind.NEW_SESSION or decision.project_id is None:
            return ChannelIngressResult(decision=decision)

        placement = _placement_for_project(placements, decision.project_id)
        created = await self._sessions.create_session(
            user_id=user_id,
            project_id=decision.project_id,
            agent_slug=placement.agent_slug,
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
        key = self._resolver.route_key(context, project_id=decision.project_id)
        await self._bindings.upsert(user_id=user_id, key=key, session_id=session_id)
        return ChannelIngressResult(decision=decision, session_id=session_id)


def _placement_for_project(placements: list[AgentPlacement], project_id: str) -> AgentPlacement:
    for placement in placements:
        if placement.project_id == project_id:
            return placement
    raise ValueError(f"agent placement for project '{project_id}' not found")


__all__ = [
    "AgentPlacementReader",
    "ChannelIngressResult",
    "ChannelIngressService",
    "ChannelSessionRef",
    "ChannelSessionRunner",
    "ChannelThreadBindingStore",
]
