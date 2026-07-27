"""Resolve external channel mentions into project-bound agent sessions."""

from __future__ import annotations

from collections.abc import Iterable

from valuz_agent.modules.channels.schemas import (
    AgentChannelRouteDecision,
    AgentPlacement,
    ChannelMentionContext,
    ChannelRouteDecisionKind,
    ChannelRouteKey,
    ChannelThreadBinding,
)


class AgentChannelResolver:
    """Pure router for IM channel mentions.

    Platform adapters should normalize inbound webhook payloads into
    ``ChannelMentionContext``. Persistence/services provide the current agent
    placements and any thread binding. This resolver then decides whether the
    turn can continue an existing session, must open a new one, or needs a
    project clarification from the human.
    """

    def resolve(
        self,
        context: ChannelMentionContext,
        *,
        placements: Iterable[AgentPlacement],
        existing_binding: ChannelThreadBinding | None = None,
        recent_binding: ChannelThreadBinding | None = None,
    ) -> AgentChannelRouteDecision:
        active_placements = tuple(
            placement
            for placement in placements
            if self._placement_matches_agent(placement, context.mentioned_agent_slug)
        )
        by_project_id = {placement.project_id: placement for placement in active_placements}

        if not active_placements:
            return self._decision(
                context,
                ChannelRouteDecisionKind.NOT_DEPLOYED,
                reason="agent_not_deployed",
            )

        explicit = self._select_explicit_project(context, active_placements)
        if explicit is not None:
            return self._new_session(context, explicit, reason="explicit_project_match")

        for binding, reason in (
            (existing_binding, "thread_binding"),
            (recent_binding, "recent_continuation"),
        ):
            if binding is None:
                continue
            if not self._binding_matches_context(context, binding):
                continue
            if binding.project_id is None or binding.session_id is None:
                continue
            if not binding.session_accepts_turn:
                continue
            if binding.project_id not in by_project_id:
                continue
            if binding is recent_binding and not context.continuation_hint:
                continue
            if context.is_top_level_mention and binding is existing_binding:
                continue
            return self._decision(
                context,
                ChannelRouteDecisionKind.REUSE_SESSION,
                project_id=binding.project_id,
                session_id=binding.session_id,
                reason=reason,
            )

        if len(active_placements) == 1:
            return self._new_session(context, active_placements[0], reason="single_deployment")

        return self._decision(
            context,
            ChannelRouteDecisionKind.ASK_PROJECT,
            reason="multiple_deployments",
            candidates=active_placements,
        )

    @staticmethod
    def route_key(context: ChannelMentionContext, *, project_id: str) -> ChannelRouteKey:
        external_thread_id = context.external_thread_id or context.external_chat_id
        return ChannelRouteKey(
            channel_instance_id=context.channel_instance_id,
            external_chat_id=context.external_chat_id,
            external_thread_id=external_thread_id,
            agent_slug=context.mentioned_agent_slug,
            project_id=project_id,
        )

    @staticmethod
    def _placement_matches_agent(placement: AgentPlacement, mentioned_agent_slug: str) -> bool:
        return mentioned_agent_slug in {placement.agent_slug, placement.source_agent_slug}

    @staticmethod
    def _binding_matches_context(
        context: ChannelMentionContext,
        binding: ChannelThreadBinding,
    ) -> bool:
        if binding.channel_instance_id != context.channel_instance_id:
            return False
        if binding.external_chat_id != context.external_chat_id:
            return False
        if binding.agent_slug != context.mentioned_agent_slug:
            return False
        if context.external_thread_id is None:
            return True
        return binding.external_thread_id == context.external_thread_id

    @staticmethod
    def _select_explicit_project(
        context: ChannelMentionContext,
        placements: tuple[AgentPlacement, ...],
    ) -> AgentPlacement | None:
        explicit_name = _normalize_project_name(context.explicit_project_name)
        for placement in placements:
            if context.explicit_project_id and placement.project_id == context.explicit_project_id:
                return placement
            if explicit_name and _normalize_project_name(placement.project_name) == explicit_name:
                return placement
        return None

    def _new_session(
        self,
        context: ChannelMentionContext,
        placement: AgentPlacement,
        *,
        reason: str,
    ) -> AgentChannelRouteDecision:
        return self._decision(
            context,
            ChannelRouteDecisionKind.NEW_SESSION,
            project_id=placement.project_id,
            reason=reason,
        )

    @staticmethod
    def _decision(
        context: ChannelMentionContext,
        kind: ChannelRouteDecisionKind,
        *,
        reason: str,
        project_id: str | None = None,
        session_id: str | None = None,
        candidates: tuple[AgentPlacement, ...] = (),
    ) -> AgentChannelRouteDecision:
        return AgentChannelRouteDecision(
            kind=kind,
            agent_slug=context.mentioned_agent_slug,
            project_id=project_id,
            session_id=session_id,
            reason=reason,
            candidates=candidates,
        )


def _normalize_project_name(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.strip().casefold().split())
    return normalized or None


__all__ = ["AgentChannelResolver"]
