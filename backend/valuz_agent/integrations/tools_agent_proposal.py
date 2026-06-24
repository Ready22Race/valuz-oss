"""``propose_agent`` / ``list_skills`` — natural-language agent creation tools.

The agent calls ``propose_agent`` once it has gathered everything a new
Agent needs (name, instructions, brain, and *equipment* — skill slugs +
connector slugs). The handler does **not** write to the agent library —
that's the user's prerogative, applied via
``POST /v1/agents/proposals/{session_id}/confirm`` when they click
"创建并部署" on the proposal card the frontend renders in response to the
``tool_use`` event this call produces. Same "agent proposes, user
disposes" trust model as ``submit_skill``.

Why a validating no-op is enough
--------------------------------
The kernel records a ``tool_use`` event the moment any tool fires; the
frontend SSE subscriber for that session already knows ``session_id`` (it
owns the page). Pairing the event payload (the full agent spec) with the
session id at the UI layer gives the confirm endpoint everything it needs
— no server-side staging required (unlike skills, whose content lives on
disk). The handler's job is to *validate* the spec early so the model
fixes problems before the user is asked to confirm:

- skill slugs must already exist in ``valuz_skill_index`` — at session
  build ``capability_resolver.resolve_skill_slugs_to_paths`` silently
  drops unindexed slugs, so an unindexed slug would bind to nothing. The
  fix is the existing flow: author with ``skill-creator`` → ``submit_skill``
  → user saves → the slug becomes indexable.
- connector slugs must exist in ``valuz_connector`` (created via
  ``create_mcp``). OAuth connectors only work once authorized, but they
  are still bindable.

Why this lives in valuz, not the kernel
---------------------------------------
The agent library, project membership, skill index and connector catalog
are all host concerns. The kernel intentionally stays generic.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

import json
import logging
from typing import Any

# Side-effect import — surfaces ``src.core...`` on sys.path. Without this,
# the kernel package fails to resolve when this module is imported during
# app startup.
import valuz_agent.boot.kernel  # noqa: F401

from src.core.tools import ExecContext, ToolDef, ToolResult

from valuz_agent.infra.auth_context import require_current_user_id

logger = logging.getLogger(__name__)

PROPOSE_AGENT_TOOL_NAME = "propose_agent"
LIST_SKILLS_TOOL_NAME = "list_skills"
LIST_AGENTS_TOOL_NAME = "list_agents"
LIST_PROJECT_MEMBERS_TOOL_NAME = "list_project_members"
DEPLOY_AGENT_TOOL_NAME = "deploy_agent"

# Mirrors the runtimes the agent library accepts (see AgentRow.runtime).
VALID_RUNTIMES = ("claude_agent", "codex", "deepagents")
# Mirrors kernel EffortLevel / api EffortLevel.
VALID_EFFORTS = ("low", "medium", "high", "xhigh", "max")


PROPOSE_AGENT_DESCRIPTION = (
    "Propose a NEW agent for the user to review, then create and deploy into "
    "the current project. Use this when the user describes an agent they want "
    "built in natural language. Call it ONCE, after you've assembled the "
    "agent's equipment.\n\n"
    "## Check for an existing agent FIRST (accuracy)\n"
    "Before proposing a NEW agent, call `list_agents` to see if a suitable one "
    "already exists in the library — and, in a project, `list_project_members` "
    "to see who's already deployed. If a fitting agent already exists, use "
    "`deploy_agent` to add it to the project instead of creating a duplicate. "
    "Only propose a new agent when none fits.\n\n"
    "The user is shown a card to confirm; nothing is written until they "
    "approve. After calling this, STOP — do not keep editing unless the user "
    "asks for changes.\n\n"
    "## Assemble equipment FIRST\n"
    "- skills: a list of skill slugs to bind. Each slug MUST already exist in "
    "the library (be indexed). To add a skill the user doesn't have yet, author "
    "it with the skill-creator skill and call `submit_skill`; once the user "
    "saves it, its slug becomes bindable. Use `list_skills` to see existing "
    "slugs. An unindexed slug is rejected with guidance — never bind one.\n"
    "- connectors: a list of connector slugs to bind. Create connectors with "
    "`create_mcp` first; OAuth connectors must be authorized by the user to "
    "actually work, but can still be bound now.\n\n"
    "## Brain (optional)\n"
    "- runtime: one of claude_agent | codex | deepagents (default claude_agent).\n"
    "- model: a model id (default claude-sonnet-4-6). Leave default unless the "
    "user asks.\n"
    "- effort: low | medium | high | xhigh | max (optional reasoning budget).\n\n"
    "Do NOT pass a slug — the backend derives a unique one from the name.\n\n"
    "Returns JSON with ok and, on success, the validated spec echoed back."
)

PROPOSE_AGENT_PARAMETERS: dict[str, object] = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "Display name of the agent. Required.",
        },
        "instructions": {
            "type": "string",
            "description": (
                "The agent's system prompt / working method (role, method, "
                "output discipline, boundaries). Required."
            ),
        },
        "description": {
            "type": "string",
            "description": "One-line description shown in the library.",
        },
        "runtime": {
            "type": "string",
            "enum": list(VALID_RUNTIMES),
            "description": "Runtime engine. Default claude_agent.",
        },
        "model": {
            "type": "string",
            "description": "Model id. Default claude-sonnet-4-6.",
        },
        "effort": {
            "type": "string",
            "enum": list(VALID_EFFORTS),
            "description": "Optional reasoning-effort budget. Omit for SDK default.",
        },
        "skills": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Skill slugs to bind. Each must already be indexed in the "
                "library (see the tool description)."
            ),
        },
        "connectors": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Connector slugs to bind (created via create_mcp).",
        },
        "avatar": {
            "type": "string",
            "description": "Optional preset avatar key or asset URL.",
        },
    },
    "required": ["name", "instructions"],
}


def _as_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if isinstance(v, (str, int)) and str(v).strip()]


async def _propose_agent_handler(args: dict[str, Any], context: ExecContext) -> ToolResult:
    """Validate the proposed agent spec; never write. The frontend renders a
    confirmation card from the ``tool_use`` event and the user's confirm call
    does the actual create + deploy."""
    name = str(args.get("name") or "").strip()
    instructions = str(args.get("instructions") or "").strip()
    if not name:
        return _err("propose_agent: 'name' is required")
    if not instructions:
        return _err("propose_agent: 'instructions' is required")

    runtime = str(args.get("runtime") or "claude_agent").strip()
    if runtime not in VALID_RUNTIMES:
        return _err(
            f"propose_agent: invalid runtime '{runtime}' — must be one of "
            f"{', '.join(VALID_RUNTIMES)}"
        )

    effort = args.get("effort")
    if effort is not None and str(effort).strip() and str(effort) not in VALID_EFFORTS:
        return _err(
            f"propose_agent: invalid effort '{effort}' — must be one of "
            f"{', '.join(VALID_EFFORTS)}"
        )

    skills = _as_str_list(args.get("skills"))
    connectors = _as_str_list(args.get("connectors"))

    # Validate equipment exists. Unindexed skills are a hard error (they would
    # silently bind to nothing); missing connectors are a soft warning
    # (mirrors create_mcp's credentials_required guidance) since the user may
    # be about to create them.
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.modules.connectors.datastore import ConnectorDatastore
    from valuz_agent.modules.skills.datastore import SkillDatastore

    user_id = require_current_user_id()
    missing_skills: list[str] = []
    missing_connectors: list[str] = []
    async with async_unit_of_work(commit=False) as db:
        if skills:
            indexed = {r.slug for r in await SkillDatastore(db).list_skills(user_id)}
            missing_skills = [s for s in skills if s not in indexed]
        if connectors:
            cds = ConnectorDatastore(db)
            for slug in connectors:
                if await cds.get_by_slug(user_id, slug) is None:
                    missing_connectors.append(slug)

    if missing_skills:
        return _err(
            "propose_agent: these skill slugs are not in the library yet, so "
            "they can't be bound: "
            + ", ".join(missing_skills)
            + ". Author each one with the skill-creator skill and call "
            "submit_skill; once the user saves it, retry. Use list_skills to "
            "see available slugs."
        )

    spec = {
        "name": name,
        "instructions": instructions,
        "description": str(args.get("description") or ""),
        "runtime": runtime,
        "model": str(args.get("model") or "claude-sonnet-4-6"),
        "effort": (str(effort).strip() or None) if effort is not None else None,
        "skills": skills,
        "connectors": connectors,
        "avatar": (str(args.get("avatar")).strip() or None) if args.get("avatar") else None,
    }
    logger.info(
        "propose_agent: name=%s runtime=%s skills=%d connectors=%d (missing_conn=%s)",
        name,
        runtime,
        len(skills),
        len(connectors),
        missing_connectors,
    )
    return ToolResult(
        content=json.dumps(
            {
                "ok": True,
                "spec": spec,
                "warnings": (
                    [
                        "These connector slugs don't exist yet; create them with "
                        "create_mcp before the user confirms: "
                        + ", ".join(missing_connectors)
                    ]
                    if missing_connectors
                    else []
                ),
                "next_step": (
                    "Proposed for the user's review. They will see a card to "
                    "create and deploy this agent into the current project. "
                    "Stop here — do not keep editing unless the user asks."
                ),
            },
            ensure_ascii=False,
        )
    )


LIST_SKILLS_DESCRIPTION = (
    "List the skills already in the user's library (slug, name, description), "
    "so you can bind existing skills by slug when proposing an agent. "
    "Read-only."
)

LIST_SKILLS_PARAMETERS: dict[str, object] = {"type": "object", "properties": {}}


async def _list_skills_handler(args: dict[str, Any], context: ExecContext) -> ToolResult:
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.modules.skills.datastore import SkillDatastore

    user_id = require_current_user_id()
    async with async_unit_of_work(commit=False) as db:
        rows = await SkillDatastore(db).list_skills(user_id)
    items = [
        {"slug": r.slug, "name": r.name, "description": (r.description or "")[:200]}
        for r in rows
    ]
    return ToolResult(content=json.dumps({"ok": True, "skills": items}, ensure_ascii=False))


LIST_AGENTS_DESCRIPTION = (
    "List the agents already in the library (slug, name, description, source). "
    "Call this BEFORE proposing a new agent so you can reuse an existing one "
    "(via deploy_agent) instead of creating a duplicate. Read-only."
)

LIST_AGENTS_PARAMETERS: dict[str, object] = {"type": "object", "properties": {}}


async def _list_agents_handler(args: dict[str, Any], context: ExecContext) -> ToolResult:
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.modules.agents.datastore import AgentDatastore

    user_id = require_current_user_id()
    async with async_unit_of_work(commit=False) as db:
        rows = await AgentDatastore(db).list_agents(user_id)
    items = [
        {
            "slug": r.slug,
            "name": r.name,
            "description": (r.description or "")[:200],
            "source": r.source,
        }
        for r in rows
    ]
    return ToolResult(content=json.dumps({"ok": True, "agents": items}, ensure_ascii=False))


LIST_PROJECT_MEMBERS_DESCRIPTION = (
    "List the agents already deployed into THIS project (their project-local "
    "handle + the library agent each references). Project sessions only — "
    "returns an error in a quick chat / agent-only conversation. Call this "
    "before deploying so you don't deploy a duplicate. Read-only."
)

LIST_PROJECT_MEMBERS_PARAMETERS: dict[str, object] = {"type": "object", "properties": {}}


async def _list_project_members_handler(
    args: dict[str, Any], context: ExecContext
) -> ToolResult:
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.modules.agents.datastore import AgentDatastore, ProjectMemberDatastore

    user_id = require_current_user_id()
    project_id = await _resolve_project_id(context.session_id)
    if not project_id:
        return _err(
            "list_project_members: this session has no project — members can "
            "only be listed inside a project. In a quick chat, use list_agents "
            "to browse the library."
        )
    async with async_unit_of_work(commit=False) as db:
        members = await ProjectMemberDatastore(db).list_by_project(user_id, project_id)
        ads = AgentDatastore(db)
        items = []
        for m in members:
            name = None
            if m.source_agent_slug:
                src = await ads.get_agent(user_id, m.source_agent_slug)
                name = src.name if src else None
            items.append(
                {
                    "agent_slug": m.agent_slug,
                    "source_agent_slug": m.source_agent_slug,
                    "name": name,
                }
            )
    return ToolResult(
        content=json.dumps(
            {"ok": True, "project_id": project_id, "members": items}, ensure_ascii=False
        )
    )


DEPLOY_AGENT_DESCRIPTION = (
    "Deploy an EXISTING library agent into THIS project (派驻 — a live "
    "reference, not a copy). Use this to reuse an agent that already exists "
    "(found via list_agents) instead of creating a new one with propose_agent. "
    "Project sessions only. Pass the library agent's slug. Idempotent: an "
    "agent already deployed to this project is reported as such, not "
    "duplicated."
)

DEPLOY_AGENT_PARAMETERS: dict[str, object] = {
    "type": "object",
    "properties": {
        "agent_slug": {
            "type": "string",
            "description": "Slug of the library agent to deploy (from list_agents).",
        }
    },
    "required": ["agent_slug"],
}


async def _deploy_agent_handler(args: dict[str, Any], context: ExecContext) -> ToolResult:
    slug = str(args.get("agent_slug") or "").strip()
    if not slug:
        return _err("deploy_agent: 'agent_slug' is required")

    project_id = await _resolve_project_id(context.session_id)
    if not project_id:
        return _err(
            "deploy_agent: this session has no project — an agent can only be "
            "deployed inside a project. In a quick chat, use propose_agent to "
            "create a new agent (no deployment)."
        )

    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.modules.agents.service import (
        AgentNotFoundError,
        AgentService,
        MemberAlreadyExistsError,
    )

    user_id = require_current_user_id()
    async with async_unit_of_work() as db:
        svc = AgentService(db)
        try:
            result = await svc.deploy_agent(user_id, project_id, slug)
        except AgentNotFoundError:
            return _err(
                f"deploy_agent: no library agent with slug '{slug}'. Call "
                "list_agents to see valid slugs."
            )
        except MemberAlreadyExistsError:
            return ToolResult(
                content=json.dumps(
                    {
                        "ok": True,
                        "already_deployed": True,
                        "next_step": f"Agent '{slug}' is already deployed to this project.",
                    },
                    ensure_ascii=False,
                )
            )
        member = result["member"]
    return ToolResult(
        content=json.dumps(
            {
                "ok": True,
                "deployed": True,
                "project_id": project_id,
                "agent_slug": member.agent_slug,
                "source_agent_slug": member.source_agent_slug,
                "next_step": "Deployed into the project; it's now an active member.",
            },
            ensure_ascii=False,
        )
    )


async def _resolve_project_id(session_id: str) -> str | None:
    """REAL project id for the calling session, or None.

    A session always carries ``metadata.valuz.project_id``, but a quick chat /
    新对话 binds to an ephemeral ``ProjectRow(kind="chat")`` that is NOT a
    deployable project. So we resolve the id then confirm ``kind == "project"``
    — chat / temp / missing all resolve to None, gating member-listing and
    deploy to real projects only."""
    if not session_id:
        return None
    from valuz_agent.adapters import kernel_client
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.modules.projects.datastore import ProjectDatastore

    user_id = require_current_user_id()
    sess = await kernel_client.get_session(user_id, session_id)
    if sess is None:
        return None
    project_id = ((sess.metadata or {}).get("valuz", {}) or {}).get("project_id") or None
    if not project_id:
        return None
    async with async_unit_of_work(commit=False) as db:
        row = await ProjectDatastore(db).get_by_id(user_id, project_id)
    return project_id if (row is not None and row.kind == "project") else None


def _err(message: str) -> ToolResult:
    return ToolResult(content=message, is_error=True)


def build_agent_proposal_tool_defs() -> tuple[ToolDef, ...]:
    """Return the agent-creation toolset for the host toolkit MCP:
    propose_agent (create new, with confirm card) + the discovery/reuse tools
    list_skills / list_agents / list_project_members / deploy_agent."""
    return (
        ToolDef(
            name=PROPOSE_AGENT_TOOL_NAME,
            description=PROPOSE_AGENT_DESCRIPTION,
            parameters=PROPOSE_AGENT_PARAMETERS,
            handler=_propose_agent_handler,
            read_only=False,
        ),
        ToolDef(
            name=LIST_SKILLS_TOOL_NAME,
            description=LIST_SKILLS_DESCRIPTION,
            parameters=LIST_SKILLS_PARAMETERS,
            handler=_list_skills_handler,
            read_only=True,
        ),
        ToolDef(
            name=LIST_AGENTS_TOOL_NAME,
            description=LIST_AGENTS_DESCRIPTION,
            parameters=LIST_AGENTS_PARAMETERS,
            handler=_list_agents_handler,
            read_only=True,
        ),
        ToolDef(
            name=LIST_PROJECT_MEMBERS_TOOL_NAME,
            description=LIST_PROJECT_MEMBERS_DESCRIPTION,
            parameters=LIST_PROJECT_MEMBERS_PARAMETERS,
            handler=_list_project_members_handler,
            read_only=True,
        ),
        ToolDef(
            name=DEPLOY_AGENT_TOOL_NAME,
            description=DEPLOY_AGENT_DESCRIPTION,
            parameters=DEPLOY_AGENT_PARAMETERS,
            handler=_deploy_agent_handler,
            read_only=False,
        ),
    )


__all__ = [
    "PROPOSE_AGENT_TOOL_NAME",
    "LIST_SKILLS_TOOL_NAME",
    "LIST_AGENTS_TOOL_NAME",
    "LIST_PROJECT_MEMBERS_TOOL_NAME",
    "DEPLOY_AGENT_TOOL_NAME",
    "build_agent_proposal_tool_defs",
]
