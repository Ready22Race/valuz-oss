"""Agent Pack manifest — the declarative, portable definition of a group of
agents and their equipment (skills + connectors).

This is the on-disk / on-the-wire schema described in ``docs/agent-pack/design.md``.
It is **pure data**: no provider bindings, no secrets, no executable logic.
Built-in packs (the recommended teams) ship as ``resources/agent_packs/<id>/
manifest.json``; a user export produces the same shape.

Text fields use the ``Text`` union — either a plain string (single-language,
e.g. a user export in the current UI language) or a ``{locale: string}`` map
(bilingual, used by the built-in packs). ``resolve_text`` normalizes both: a
bare string resolves to itself for every locale.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from valuz_agent.i18n import get_locale

# A localizable text value: a bare string (same for all locales) or a
# ``{locale: text}`` map. See module docstring.
Text = str | dict[str, str]

_FALLBACK_LOCALE = "en-US"


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


class PackSkill(BaseModel):
    """One skill the pack's agents reference.

    ``embedded`` → files live under the pack's ``skills/<slug>/`` (user export).
    ``bundled``  → shipped with the app (built-in packs); materialized from
    ``resources/template_skills/`` on import, never carried in the pack.
    """

    slug: str
    source: str = "embedded"  # embedded | bundled
    name: Text | None = None
    description: Text | None = None


class PackConnector(BaseModel):
    """One connector (MCP) the pack's agents reference — a pure pointer.

    Never carries code or secrets. ``source=catalog`` references an app-shipped
    connector by slug (built-in packs); ``source=custom`` carries a full
    user-defined definition (URL / command), credentials stripped.
    ``requires_credentials`` / ``requires_setup`` drive the import "to configure"
    tray (supply a key / deploy a local MCP).
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
    ``(provider_id, model)`` is re-resolved against the target machine's
    channel at import time. ``skills`` / ``connectors`` reference the pack's
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
    """Display header for the pack — always present, so there is exactly ONE
    manifest shape to parse (a single-agent export is just "a group of one").
    Purely presentational; importing a pack creates no persistent "team" record.
    ``id`` is meaningful only for the built-in packs (their stable identity);
    user exports leave it unset (null)."""

    id: str | None = None
    name: Text
    description: Text = ""
    scenario: Text = ""
    icon: str | None = None


class AgentPackManifest(BaseModel):
    """The root manifest — a collection header + 1..N agents plus shared skill /
    connector indexes. ``collection`` is always present (one structure)."""

    schema_version: int = 1
    kind: str = "agent-pack"
    collection: PackCollection
    agents: list[PackAgent]
    skills: list[PackSkill] = Field(default_factory=list)
    connectors: list[PackConnector] = Field(default_factory=list)
