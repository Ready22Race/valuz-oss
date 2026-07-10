"""Unified Valuz Pack manifest (schema v2) — one declarative JSON shape for
both agent packs and project packs, plus the shared portable atoms.

Pure data (Pydantic models): no secrets, no provider bindings, no machine-local
ids. This module is the base of the pack format — it owns the atoms
(``Text`` / ``PackSkill`` / ``PackConnector`` / ``PackAgent`` / ``PackCollection``)
and the unified :class:`PackManifest`. The legacy v1 agent root
``AgentPackManifest`` (``agent_packs.manifest``) builds on these atoms and is
accepted by the reader for back-compat; the legacy project-pack format is not.

Top-level shape::

    {
      "schema_version": 2,
      "kind": "valuz-pack",
      "agents": [...], "skills": [...], "connectors": [...],   # payload
      "collection": {...}   # XOR
      "project":    {...}
    }

Exactly one of ``collection`` / ``project`` is present (enforced by a
validator). The reader also accepts legacy v1 agent packs and lifts them into
this shape via :func:`from_legacy_agent_pack`.

``Text`` is a localizable value: a bare string (same for all locales) or a
``{locale: text}`` map (the built-in packs are bilingual). ``resolve_text``
normalizes both — a bare string resolves to itself for every locale.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field, model_validator

from valuz_agent.i18n import get_locale

if TYPE_CHECKING:
    from valuz_agent.modules.agent_packs.manifest import AgentPackManifest

SCHEMA_VERSION = 2
KIND = "valuz-pack"

# A localizable text value: a bare string (same for all locales) or a
# ``{locale: text}`` map.
Text = str | dict[str, str]

_FALLBACK_LOCALE = "en-US"

__all__ = [
    "KIND",
    "SCHEMA_VERSION",
    "PackAgent",
    "PackAutomation",
    "PackCollection",
    "PackConnector",
    "PackManifest",
    "PackMember",
    "PackProject",
    "PackProjectConnector",
    "PackProjectSkillConfig",
    "PackSkill",
    "Text",
    "from_legacy_agent_pack",
    "resolve_text",
]


def resolve_text(value: Text | None, locale: str | None = None) -> str:
    """Resolve a ``Text`` to a concrete string for ``locale``.

    A bare string returns itself. A locale map prefers the requested locale,
    then the fallback locale, then any available value. ``None`` → empty string.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    loc = locale or get_locale()
    if loc in value:
        return value[loc]
    if _FALLBACK_LOCALE in value:
        return value[_FALLBACK_LOCALE]
    return next(iter(value.values()), "")


# ----------------------------------------------------------------------------
# Portable atoms (shared payload records)
# ----------------------------------------------------------------------------


class PackSkill(BaseModel):
    """One skill the pack's agents reference.

    ``embedded`` → files live under the pack's ``skills/<slug>/`` (user export).
    ``bundled``  → shipped with the app (built-in packs); materialized from
    ``resources/template_skills/`` on import, never carried in the pack.
    ``skillhub`` → marketplace-curated dependency; the marketplace installer
    downloads it from SkillHub before creating the pack's agents.
    """

    slug: str
    source: str = "embedded"  # embedded | bundled | skillhub
    name: Text | None = None
    description: Text | None = None


class PackConnector(BaseModel):
    """One connector (MCP) the pack's agents reference — a pure pointer.

    Never carries code or secrets. ``source=catalog`` references an app-shipped
    connector by slug; ``source=custom`` carries a full user-defined definition
    (URL / command), credentials stripped. ``requires_credentials`` /
    ``requires_setup`` drive the import "to configure" tray.
    """

    slug: str
    source: str = "catalog"  # catalog | custom
    display_name: Text | None = None
    description: Text | None = None
    transport: str = "stdio"  # http | sse | stdio
    auth_type: str = "none"  # none | bearer | oauth
    requires_credentials: bool = False
    requires_setup: bool = False
    url: str | None = None
    command: str | None = None
    args: list[str] | None = None
    oauth_metadata: dict[str, Any] | None = None
    setup_hint: Text | None = None
    credentials_help_url: str | None = None


class PackAgent(BaseModel):
    """One agent in the pack — the portable definition.

    ``runtime`` + ``model_hint`` are portable hints; the concrete
    ``(provider_id, model)`` is re-resolved against the target machine's channel
    at import time. ``skills`` / ``connectors`` reference the pack's
    ``skills[]`` / ``connectors[]`` by slug.
    """

    slug: str
    name: Text
    description: Text = ""
    instructions: Text = ""
    avatar: str | None = None
    runtime: str = "claude_agent"
    model_hint: str | None = None
    effort: str | None = None
    skills: list[str] = Field(default_factory=list)
    connectors: list[str] = Field(default_factory=list)


class PackCollection(BaseModel):
    """Display header for an agent collection — purely presentational. Importing
    a collection creates no persistent "team" record. ``id`` is meaningful only
    for the built-in packs (their stable identity); user exports leave it null.
    """

    id: str | None = None
    name: Text
    description: Text = ""
    scenario: Text = ""
    icon: str | None = None


# ----------------------------------------------------------------------------
# Project target records
# ----------------------------------------------------------------------------


class PackMember(BaseModel):
    """One project team member — a project-local ``agent_slug`` handle pointing
    at a library agent by ``source_agent_slug``.

    Slim by design: the agent definition itself lives once in the top-level
    ``agents[]`` payload (referenced by ``source_agent_slug``), so deploying the
    same library agent under two handles costs one ``agents[]`` entry and two
    members. ``agent_slug`` is what automations key on, so the recipient must
    preserve it.
    """

    agent_slug: str
    source_agent_slug: str | None = None


class PackAutomation(BaseModel):
    """Flat port of the portable ``valuz_automation`` columns. Runtime/local
    columns (``id`` / ``user_id`` / ``next_run_at`` / ...) are dropped; the
    trigger columns stay flat so the recipient re-validates them on import."""

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
    """A project-scoped enabled skill path. Absolute on the source machine; the
    recipient treats it as best-effort and skips entries that don't resolve."""

    skill_path: str


class PackProjectConnector(BaseModel):
    """A project-scoped connector — just a slug, resolved against the
    recipient's connector catalog at import."""

    slug: str


class PackProject(BaseModel):
    """The ``project`` target — a first-class project to create on import,
    deploying the payload agents as members.

    ``members`` reference the top-level ``agents[]`` by ``source_agent_slug``;
    ``skills`` / ``connectors`` here are the **project-scoped** configs (enabled
    skill paths + connector slugs), distinct from the payload indexes.
    ``memory`` is an archive-relative pointer to the on-disk memory tree (set by
    the packager to ``"memory"`` when a memory dir is carried; ``None`` /
    omitted otherwise) so the manifest self-describes the memory payload.
    """

    name: str
    kind: str = "project"
    icon: str | None = None
    instructions_md: Text = ""
    members: list[PackMember] = Field(default_factory=list)
    automations: list[PackAutomation] = Field(default_factory=list)
    skills: list[PackProjectSkillConfig] = Field(default_factory=list)
    connectors: list[PackProjectConnector] = Field(default_factory=list)
    # Archive-relative directory holding the project memory tree (e.g.
    # ``"memory"``); ``None`` when the pack carries no memory.
    memory: str | None = None


# ----------------------------------------------------------------------------
# Unified root
# ----------------------------------------------------------------------------


class PackManifest(BaseModel):
    """Root unified manifest. Payload (``agents`` / ``skills`` / ``connectors``)
    plus exactly one target (``collection`` XOR ``project``)."""

    schema_version: int = SCHEMA_VERSION
    kind: str = KIND
    agents: list[PackAgent] = Field(default_factory=list)
    skills: list[PackSkill] = Field(default_factory=list)
    connectors: list[PackConnector] = Field(default_factory=list)
    collection: PackCollection | None = None
    project: PackProject | None = None

    @model_validator(mode="after")
    def _exactly_one_target(self) -> PackManifest:
        if bool(self.collection) is bool(self.project):
            raise ValueError(
                "manifest must carry exactly one of `collection` or `project`"
            )
        return self


# ----------------------------------------------------------------------------
# Legacy (v1) → unified (v2) lifters
# ----------------------------------------------------------------------------


def from_legacy_agent_pack(m: AgentPackManifest) -> PackManifest:
    """Lift a v1 ``agent-pack`` manifest into the unified shape (collection
    target). Field names already align — agents/skills/connectors/collection."""
    return PackManifest(
        agents=list(m.agents),
        skills=list(m.skills),
        connectors=list(m.connectors),
        collection=m.collection,
    )
