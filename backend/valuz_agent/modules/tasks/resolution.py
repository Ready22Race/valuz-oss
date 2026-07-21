"""TaskSessionResolver — host-side session resolution for task actors.

The ONE place task code turns host knowledge (project rows, membership, the
agent library, providers, credentials) into ready-to-create kernel sessions.
Kickoff, commit and dispatch used to each carry their own copy of this
resolution block; they now consume this module, and no other task file may
import ``ProjectDatastore`` / ``ProjectMemberDatastore`` for session building.

The seam stands on its own: callers receive a fully resolved session and
never learn *how* it was resolved. The interface is shaped after
``MemberResolverPort`` (docs/design/task-kernel-migration.md §5.1) so that if
the — currently deferred — kernel migration is ever revived, this class
graduates into the port's host implementation verbatim.

Error convention (mirrors ``planning.resolve_dispatch_node``): resolve
methods return the resolved value on success or a human-readable ``str`` on
failure — callers decide whether that string becomes a raise, an error dict,
or a task event.
"""

# ruff: noqa: I001
from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal, cast

from valuz_agent.adapters.agent_resolver import (
    _member_agent_config,
    build_member_session,
    embed_agent_config,
    resolve_agent_display_name,
    spill_goal_brief_if_too_long,
)
from valuz_agent.infra.fs_registry import fs_registry
from valuz_agent.modules.agents.datastore import ProjectMemberDatastore
from valuz_agent.modules.projects.datastore import ProjectDatastore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Provider deps + credential pre-flight (absorbed from _session_build.py)
# ---------------------------------------------------------------------------


def _provider_resolver_deps(db: Any) -> dict[str, Any]:
    """Build the provider deps so build_member_session
    can resolve a per-agent pinned provider into the run's model_provider."""
    from valuz_agent.modules.providers.datastore import ProviderDatastore

    return {
        "providers": ProviderDatastore(db),
    }


async def _credential_gap(
    session: Any,
    agent_slug: str,
    *,
    db: Any | None = None,
    user_id: str | None = None,
) -> str | None:
    """Return a clear reason when a built session has no usable credentials.

    Credentials are funnelled through the provider system: a session's
    resolved ``model_provider`` (base_url/api_key/protocol) is the single
    source of truth (see backend/CLAUDE.md — the host does not read LLM keys
    from process env). When ``model_provider`` is None the run would only fail
    mid-turn with a cryptic SDK "Not logged in · Please run /login", so we
    detect it up front and surface *why* (no model provider configured).

    Exception: OAuth subscription providers (``claude /login`` /
    ``codex /login``) deliberately resolve to ``model_provider=None`` —
    their credentials live in the CLI's keychain and the runtime SDK
    reads them out-of-band. The pinned provider's ``auth_type`` tells us
    which case we're in, so we don't false-positive on a perfectly valid
    subscription setup. When ``db`` is omitted (legacy callers) we fall
    back to the strict check.

    Returns ``None`` when a provider resolved (or is an OAuth
    subscription), else a human-readable reason.
    """
    if user_id is None:
        raise ValueError("user_id is required")

    if getattr(session, "model_provider", None) is not None:
        return None

    # session.model_provider is None — could be (a) no provider pinned,
    # or (b) pinned an OAuth subscription provider. Distinguish by
    # loading the agent and checking the pinned provider's auth_type.
    if db is not None:
        try:
            from valuz_agent.modules.providers.datastore import ProviderDatastore

            agent = getattr(session, "agent_config", None)
            if agent is not None:
                provider_id = (getattr(agent, "metadata", None) or {}).get("provider_id")
                if provider_id:
                    provider = await ProviderDatastore(db).get_by_id(user_id, provider_id)
                    if provider is not None and provider.auth_type == "oauth":
                        # CLI-managed credentials — model_provider=None is
                        # the expected resolver output, not a gap.
                        return None
        except Exception:
            logger.warning(
                "credential_gap: OAuth-provider lookup failed for agent %s — "
                "falling back to strict check.",
                agent_slug,
                exc_info=True,
            )

    runtime = getattr(session, "runtime_provider", "") or ""
    return (
        f"agent '{agent_slug}' has no model provider configured "
        f"(runtime '{runtime}'). Pin a model provider on the agent before "
        f"dispatching."
    )


# ---------------------------------------------------------------------------
# Lead clone
# ---------------------------------------------------------------------------


def materialize_lead_clone(
    base_agent: Any, dispatch_mode: Literal["sync", "async"] = "sync"
) -> Any:  # returns the lead-clone AgentConfig
    """Materialize a per-task **lead clone** of *base_agent*.

    Tool surfaces ride the session's ``harness`` MCP entry
    (``build_member_session(is_lead=True)`` points it at the ``lead``
    toolset of the host toolkit MCP server) — the clone carries no tool
    declarations of its own. It survives as an identity stamp: the
    ``__lead__{mode}`` id marks the embedded snapshot as a lead clone for
    queries/diagnostics. Stable per (base, mode); the clone exists only as
    the lead session's embedded snapshot — the kernel has no agents table.
    """
    clone_id = f"{base_agent.id}__lead__{dispatch_mode}"
    return replace(base_agent, id=clone_id, tools=())


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskProjectEnv:
    """Host project facts every task-session build needs.

    ``project_cwd`` is the MAIN project cwd — the anchor for brief spills and
    task narrative files even when the task itself runs in a worktree
    (design §5/R5: coordination files must never dirty the worktree).
    """

    project_row: Any
    project_cwd: Path
    instructions_md: str | None


@dataclass(frozen=True)
class ResolvedTaskSession:
    """A ready-to-create kernel session plus the facts callers persist.

    ``brief`` is the post-spill text (used as both the embedded brief and the
    initial ``/goal`` prompt). ``credential_gap`` is the fail-fast pre-flight
    result — the caller decides how to surface it (raise / error dict /
    ``subtask_failed`` event). ``agent_name`` is the durable display name
    members stamp into event payloads (leads don't need it).
    """

    session: Any
    agent_slug: str
    brief: str
    credential_gap: str | None
    agent_name: str | None = None


class TaskSessionResolver:
    """Resolve host definitions into task lead / member kernel sessions.

    Methods run inside the caller's unit of work (they take ``db``) so the
    extraction changes no transaction boundary. Resolution is fully awaited
    before the caller's synchronous spawn block — the §5.2 timing invariant
    (no ``await`` between registry add and ``create_task``) stays intact.
    """

    async def resolve_project_env(
        self, db: Any, *, user_id: str, project_id: str
    ) -> TaskProjectEnv | None:
        """Project row + main cwd + instructions, or ``None`` when missing."""
        ws_ds = ProjectDatastore(db)
        ws_row = await ws_ds.get_by_id(user_id, project_id)
        if ws_row is None:
            return None
        project_cwd = fs_registry.project_cwd(
            user_id,
            ws_row.id,
            cast(
                Literal["chat", "project"],
                ws_row.kind if ws_row.kind in ("chat", "project") else "chat",
            ),
            ws_row.root_path,
        )
        ws_ctx = await ws_ds.get_context(user_id, project_id)
        return TaskProjectEnv(
            project_row=ws_row,
            project_cwd=project_cwd,
            instructions_md=ws_ctx.instructions_md if ws_ctx else None,
        )

    async def resolve_lead(
        self,
        db: Any,
        *,
        env: TaskProjectEnv,
        project_id: str,
        task_id: str,
        agent_slug: str,
        cwd: str,
        brief: str,
        user_id: str,
        dispatch_mode: Literal["sync", "async"] = "async",
        plan_pre_committed: bool = False,
        worktree_notice: str | None = None,
    ) -> ResolvedTaskSession | str:
        """Resolve a task lead session (kickoff and commit share this path).

        Fetches the lead's membership, materializes the per-task lead clone,
        spills an over-cap brief (anchored at the MAIN project cwd), builds
        the kernel session and runs the credential pre-flight.
        """
        member_ds = ProjectMemberDatastore(db)
        lead_member = await member_ds.get(user_id, project_id, agent_slug)
        if lead_member is None:
            return f"lead agent {agent_slug!r} is not a member of project {project_id!r}"
        lead_agent = await _member_agent_config(lead_member, member_ds, user_id=user_id)
        lead_clone = (
            materialize_lead_clone(lead_agent, dispatch_mode) if lead_agent is not None else None
        )

        brief = spill_goal_brief_if_too_long(
            brief,
            run_dir=str(env.project_cwd),
            task_id=task_id,
            label=agent_slug,
            is_lead=True,
        )

        session = await build_member_session(
            project_id=project_id,
            agent_slug=agent_slug,
            members=member_ds,
            is_lead=True,
            task_id=task_id,
            run_dir=cwd,
            brief=brief,
            project_name=env.project_row.name,
            project_instructions_md=env.instructions_md,
            dispatch_mode=dispatch_mode,
            # Lead runs the whole task in goal mode: the kernel auto-loops
            # until the task goal is met. ``finish_task`` remains the
            # authoritative terminal (it forces mode back to default).
            goal_mode=True,
            plan_pre_committed=plan_pre_committed,
            worktree_notice=worktree_notice,
            user_id=user_id,
            **_provider_resolver_deps(db),
        )
        if session is None:
            return f"could not build lead session for {agent_slug!r}"
        if lead_clone is not None:
            session = embed_agent_config(session, lead_clone)

        gap = await _credential_gap(session, agent_slug, db=db, user_id=user_id)
        return ResolvedTaskSession(
            session=session,
            agent_slug=agent_slug,
            brief=brief,
            credential_gap=gap,
        )

    async def resolve_member(
        self,
        db: Any,
        *,
        env: TaskProjectEnv,
        project_id: str,
        task_id: str,
        agent_slug: str,
        run_dir: str,
        brief: str,
        user_id: str,
        spill_label: str | None = None,
        lead_session_id: str | None = None,
        worktree_notice: str | None = None,
    ) -> ResolvedTaskSession | str:
        """Resolve a dispatched member's sub-run session.

        Members run in goal mode (self-loop until the scoped goal is met,
        then auto-exit and report ``member_done``); the lead's
        ``review_subtask`` authority is unchanged. The display name is
        captured here — where the agent is guaranteed resolvable — so events
        carry a durable ``agent_name`` instead of a racy frontend join.
        """
        member_ds = ProjectMemberDatastore(db)

        brief = spill_goal_brief_if_too_long(
            brief,
            run_dir=str(env.project_cwd),
            task_id=task_id,
            label=spill_label or agent_slug,
            is_lead=False,
        )

        session = await build_member_session(
            project_id=project_id,
            agent_slug=agent_slug,
            members=member_ds,
            is_lead=False,
            task_id=task_id,
            run_dir=run_dir,
            brief=brief,
            project_name=env.project_row.name,
            project_instructions_md=env.instructions_md,
            lead_session_id=lead_session_id,
            goal_mode=True,
            worktree_notice=worktree_notice,
            user_id=user_id,
            **_provider_resolver_deps(db),
        )
        if session is None:
            return f"agent {agent_slug!r} not found in project {project_id!r}"

        agent_name = await resolve_agent_display_name(project_id, agent_slug, user_id)
        gap = await _credential_gap(session, agent_slug, db=db, user_id=user_id)
        return ResolvedTaskSession(
            session=session,
            agent_slug=agent_slug,
            brief=brief,
            credential_gap=gap,
            agent_name=agent_name,
        )


task_session_resolver = TaskSessionResolver()

__all__ = [
    "ResolvedTaskSession",
    "TaskProjectEnv",
    "TaskSessionResolver",
    "_credential_gap",
    "_provider_resolver_deps",
    "materialize_lead_clone",
    "task_session_resolver",
]
