"""Valuz Pack — the unified, portable ``.valuzpack`` format shared by agent
exports and project exports.

A pack is one zip with a single ``manifest.json`` contract. The manifest always
carries the installable **payload** (``agents`` / ``skills`` / ``connectors``)
and exactly one **target** describing how that payload is grouped:

- ``collection`` — a display-only grouping (an agent pack); import installs the
  agents into the library and creates no persistent record.
- ``project`` — a first-class project (a project pack); import installs the
  payload **and** creates a project that deploys the agents as members, with
  automations, project-scoped skills/connectors, and an on-disk ``memory/`` tree.

``collection`` is the degenerate case of ``project`` (agents only, nothing
persisted), so the two are sibling named fields rather than a synthetic parent
type. See ``docs/agent-pack/design.md`` for the full spec.

The reader accepts legacy v1 agent packs too (``kind: agent-pack``) and
normalizes them into the unified v2 model, so already-exported agent packs keep
importing. The legacy ``.valuz-project`` project-pack format is rejected.
"""

from valuz_agent.modules.packs_common.archive import (
    MANIFEST_NAME,
    MEMORY_DIR,
    SKILLS_DIR,
    PackArchiveError,
    build_archive,
    embedded_skill_dir,
    extract_archive,
    memory_root,
    sanitize_skill_slug,
)
from valuz_agent.modules.packs_common.manifest import (
    KIND,
    SCHEMA_VERSION,
    PackAutomation,
    PackManifest,
    PackMember,
    PackProject,
    PackProjectConnector,
    PackProjectSkillConfig,
)

__all__ = [
    "KIND",
    "MANIFEST_NAME",
    "MEMORY_DIR",
    "SCHEMA_VERSION",
    "SKILLS_DIR",
    "PackArchiveError",
    "PackAutomation",
    "PackManifest",
    "PackMember",
    "PackProject",
    "PackProjectConnector",
    "PackProjectSkillConfig",
    "build_archive",
    "embedded_skill_dir",
    "extract_archive",
    "memory_root",
    "sanitize_skill_slug",
]
