"""Agent Pack module — the declarative import/export format for a group of
agents and their equipment. Built-in packs are the recommended teams; the same
format backs user import/export. See ``docs/agent-pack/design.md``."""

from valuz_agent.modules.agent_packs.errors import PackNotFound
from valuz_agent.modules.agent_packs.loader import (
    BUILTIN_PACK_IDS,
    ONBOARDING_PACK_IDS,
    load_builtin_pack,
    load_builtin_packs,
)
from valuz_agent.modules.agent_packs.manifest import AgentPackManifest, resolve_text
from valuz_agent.modules.agent_packs.service import AgentPackService

__all__ = [
    "AgentPackManifest",
    "AgentPackService",
    "BUILTIN_PACK_IDS",
    "ONBOARDING_PACK_IDS",
    "PackNotFound",
    "load_builtin_pack",
    "load_builtin_packs",
    "resolve_text",
]
