"""Project Pack manifest — the portable definition of a project for
``.valuz-project`` export/import.

Pure data (Pydantic models), no secrets, no machine-local bindings. The
shape is intentionally a superset of an ``AgentPackManifest``: the same
``PackAgent`` / ``PackSkill`` / ``PackConnector`` records are reused so the
underlying ``agent_packs`` machinery can install a project's agents,
skills and connectors unchanged. On top of that, a project pack carries
project metadata, the team members (each linking its project-local
``agent_slug`` handle to a full ``PackAgent`` snapshot of the source
library agent), automations, project-scoped skill paths, project-scoped
connector slugs, and the project memory directory.

Text fields use the ``Text`` union from ``agent_packs.manifest``.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from valuz_agent.modules.agent_packs.manifest import (
    PackAgent,
    PackConnector,
    PackSkill,
    Text,
)


class ProjectMeta(BaseModel):
    """Project identity + user-authored prompt — the part of
    ``valuz_project`` that's portable across machines (``id`` /
    ``root_path`` / ``sort_order`` are machine-local and dropped)."""

    name: str
    kind: str = "project"
    icon: str | None = None
    instructions_md: Text = ""


class PackMember(BaseModel):
    """One project team member — a project-local ``agent_slug`` handle
    pointing at the source library agent via its full ``PackAgent``
    snapshot.

    ``agent_slug`` is the project-local member handle the recipient must
    preserve so automations (which key on it) keep resolving.
    ``source_agent_slug`` is the library slug the snapshot represents; the
    recipient reuses it for de-duplication.
    """

    agent_slug: str
    source_agent_slug: str | None = None
    agent: PackAgent


class PackAutomation(BaseModel):
    """Flat port of the ``valuz_automation`` columns that are portable.

    Dropped on purpose: ``id``, ``user_id``, ``origin_tool_call_id``,
    ``next_run_at``, ``last_run_at`` (all runtime / machine-local). The
    trigger columns are flat (not a union) so the recipient re-validates
    them at import time against its own CHECK constraints.
    """

    name: str
    agent_kind: str
    agent_slug: str
    prompt_template: str
    action_kind: str = "chat"
    trigger_kind: str
    cron_expr: str | None = None
    timezone: str | None = None
    interval_seconds: int | None = None
    status: str = "enabled"


class PackProjectSkillConfig(BaseModel):
    """A project-scoped enabled skill path (``valuz_project_skill_config``).

    The path may be absolute on the source machine; the recipient treats
    it as best-effort and skips entries that don't resolve locally.
    """

    skill_path: str


class PackProjectConnector(BaseModel):
    """A project-scoped connector (``valuz_project_connector``) — just a
    slug, resolved against the recipient's connector catalog at import."""

    slug: str


class ProjectPackManifest(BaseModel):
    """The root project-pack manifest.

    ``skills`` / ``connectors`` are the shared indexes (same role as in
    ``AgentPackManifest``); ``members`` reference them by slug through the
    embedded ``PackAgent`` snapshots.
    """

    schema_version: int = 1
    kind: str = "project-pack"
    project: ProjectMeta
    members: list[PackMember] = Field(default_factory=list)
    automations: list[PackAutomation] = Field(default_factory=list)
    project_skills: list[PackProjectSkillConfig] = Field(default_factory=list)
    project_connectors: list[PackProjectConnector] = Field(default_factory=list)
    skills: list[PackSkill] = Field(default_factory=list)
    connectors: list[PackConnector] = Field(default_factory=list)
