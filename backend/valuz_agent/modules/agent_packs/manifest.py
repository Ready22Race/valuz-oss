"""Agent Pack manifest — the legacy v1 root for a group of agents and their
equipment (skills + connectors).

The portable **atoms** (``Text`` / ``resolve_text`` / ``PackSkill`` /
``PackConnector`` / ``PackAgent`` / ``PackCollection``) now live in
``packs_common.manifest`` (the base of the unified pack format) and are
re-exported here so existing imports keep working. This module keeps only the
v1 :class:`AgentPackManifest` root, which the built-in pack loader and the
back-compat reader still use. New exports produce the unified
``packs_common.manifest.PackManifest`` (schema v2) instead.

See ``docs/agent-pack/design.md``.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from valuz_agent.modules.packs_common.manifest import (
    PackAgent,
    PackCollection,
    PackConnector,
    PackSkill,
    Text,
    resolve_text,
)

__all__ = [
    "AgentPackManifest",
    "PackAgent",
    "PackCollection",
    "PackConnector",
    "PackSkill",
    "Text",
    "resolve_text",
]


class AgentPackManifest(BaseModel):
    """The legacy v1 root manifest — a collection header + 1..N agents plus
    shared skill / connector indexes. Kept for built-in packs (shipped as
    ``resources/agent_packs/<id>/manifest.json``) and for reading already
    exported v1 ``.valuzpack`` archives; new exports use the unified
    ``PackManifest``."""

    schema_version: int = 1
    kind: str = "agent-pack"
    collection: PackCollection
    agents: list[PackAgent]
    skills: list[PackSkill] = Field(default_factory=list)
    connectors: list[PackConnector] = Field(default_factory=list)
