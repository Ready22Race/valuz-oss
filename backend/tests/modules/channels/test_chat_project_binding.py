"""Chat ↔ project binding: "this group is that project".

Replaces inferring a chat's project from whichever session lineage happened to
be touched last — a guess that could not be inspected, changed, or reasoned
about. See docs/design/channel-project-binding-and-default-lead.md §3.2, §4.1.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.infra.database import Base
from valuz_agent.modules.channels import (
    AgentChannelResolver,
    AgentPlacement,
    ChannelMentionContext,
    ChannelRouteDecisionKind,
    ChannelThreadBinding,
)
from valuz_agent.modules.channels.datastore import ChannelChatBindingDatastore
from valuz_agent.modules.channels.models import ChannelChatBindingRow


@pytest.fixture
def sessionmaker_(tmp_path):
    db_file = tmp_path / "chat_bindings.db"
    sync_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(sync_engine, tables=[ChannelChatBindingRow.__table__])
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    return async_sessionmaker(bind=async_engine, expire_on_commit=False)


def _placement(project_id: str, *, agent_slug: str = "helper") -> AgentPlacement:
    return AgentPlacement(
        project_id=project_id,
        project_name=project_id,
        agent_slug=agent_slug,
        source_agent_slug=agent_slug,
    )


def _context(**overrides) -> ChannelMentionContext:
    base = dict(
        user_id="u1",
        channel_instance_id="feishu-main",
        external_chat_id="chat-1",
        external_thread_id=None,
        mentioned_agent_slug="helper",
        is_top_level_mention=True,
    )
    base.update(overrides)
    return ChannelMentionContext(**base)  # type: ignore[arg-type]


# ------------------------------------------------------------------ #
# datastore
# ------------------------------------------------------------------ #


async def test_bind_rebind_and_unbind(sessionmaker_) -> None:
    async with sessionmaker_() as db:
        ds = ChannelChatBindingDatastore(db)
        await ds.upsert(
            user_id="u1",
            channel_instance_id="feishu-main",
            external_chat_id="chat-1",
            project_id="proj-a",
            external_chat_name="研究群",
        )
        # A chat holds exactly one project — rebinding overwrites.
        rebound = await ds.upsert(
            user_id="u1",
            channel_instance_id="feishu-main",
            external_chat_id="chat-1",
            project_id="proj-b",
        )
        assert rebound.project_id == "proj-b"
        assert rebound.external_chat_name == "研究群"  # kept when not resupplied

        rows = await ds.list_all(user_id="u1")
        assert len(rows) == 1

        assert (
            await ds.delete(
                user_id="u1",
                channel_instance_id="feishu-main",
                external_chat_id="chat-1",
            )
            is True
        )
        assert (
            await ds.get(
                user_id="u1",
                channel_instance_id="feishu-main",
                external_chat_id="chat-1",
            )
            is None
        )


async def test_bindings_are_owner_scoped(sessionmaker_) -> None:
    async with sessionmaker_() as db:
        ds = ChannelChatBindingDatastore(db)
        await ds.upsert(
            user_id="u1",
            channel_instance_id="feishu-main",
            external_chat_id="chat-1",
            project_id="proj-a",
        )
        assert (
            await ds.get(
                user_id="u2",
                channel_instance_id="feishu-main",
                external_chat_id="chat-1",
            )
            is None
        )


async def test_a_project_may_be_bound_from_several_chats(sessionmaker_) -> None:
    """Allowed by design (an internal group and a client group), while a chat
    still holds exactly one project."""
    async with sessionmaker_() as db:
        ds = ChannelChatBindingDatastore(db)
        for chat in ("chat-1", "chat-2"):
            await ds.upsert(
                user_id="u1",
                channel_instance_id="feishu-main",
                external_chat_id=chat,
                project_id="proj-a",
            )
        rows = await ds.list_for_project(user_id="u1", project_id="proj-a")
        assert {row.external_chat_id for row in rows} == {"chat-1", "chat-2"}


# ------------------------------------------------------------------ #
# resolution order (§4.1)
# ------------------------------------------------------------------ #


def test_binding_decides_the_project_instead_of_asking() -> None:
    """With several placements the resolver would normally ask which project.
    A bound group has already answered that."""
    decision = AgentChannelResolver().resolve(
        _context(),
        placements=[_placement("proj-a"), _placement("proj-b")],
        chat_project_id="proj-b",
    )
    assert decision.kind == ChannelRouteDecisionKind.NEW_SESSION
    assert decision.project_id == "proj-b"
    assert decision.reason == "chat_project_binding"


def test_explicit_hint_still_outranks_the_binding() -> None:
    decision = AgentChannelResolver().resolve(
        _context(explicit_project_name="proj-a"),
        placements=[_placement("proj-a"), _placement("proj-b")],
        chat_project_id="proj-b",
    )
    assert decision.project_id == "proj-a"
    assert decision.reason == "explicit_project_match"


def test_bound_chat_continues_only_its_own_project_lineage() -> None:
    """A lineage left over from before the binding must not answer for the
    project the group now stands for."""
    stale = ChannelThreadBinding(
        channel_instance_id="feishu-main",
        external_chat_id="chat-1",
        external_thread_id="chat-1",
        agent_slug="helper",
        project_id="proj-a",
        session_id="session-a",
    )
    decision = AgentChannelResolver().resolve(
        _context(),
        placements=[_placement("proj-a"), _placement("proj-b")],
        existing_binding=stale,
        chat_project_id="proj-b",
    )
    assert decision.kind == ChannelRouteDecisionKind.NEW_SESSION
    assert decision.project_id == "proj-b"


def test_bound_chat_reuses_its_own_lineage() -> None:
    live = ChannelThreadBinding(
        channel_instance_id="feishu-main",
        external_chat_id="chat-1",
        external_thread_id="chat-1",
        agent_slug="helper",
        project_id="proj-b",
        session_id="session-b",
    )
    decision = AgentChannelResolver().resolve(
        _context(),
        placements=[_placement("proj-a"), _placement("proj-b")],
        existing_binding=live,
        chat_project_id="proj-b",
    )
    assert decision.kind == ChannelRouteDecisionKind.REUSE_SESSION
    assert decision.session_id == "session-b"


def test_binding_to_a_project_the_agent_is_not_on_degrades() -> None:
    """A binding pointing at a project the agent was never deployed to is a
    misconfiguration — fall through rather than fail session creation later."""
    decision = AgentChannelResolver().resolve(
        _context(),
        placements=[_placement("proj-a")],
        chat_project_id="proj-zzz",
    )
    assert decision.kind == ChannelRouteDecisionKind.NEW_SESSION
    assert decision.project_id == "proj-a"
    assert decision.reason == "single_deployment"
