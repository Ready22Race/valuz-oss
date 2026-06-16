"""Load the built-in Agent Packs shipped under ``resources/agent_packs/``.

Built-in packs are the recommended teams (内容 / 投研 / 产研). They are the
single source of truth for both the agent-template library and onboarding's
team recommendations — onboarding just surfaces a subset of these same packs.

``BUILTIN_PACK_IDS`` is display order. The library may grow more packs later;
onboarding's recommended set is ``ONBOARDING_PACK_IDS`` (currently all of them).
"""

from __future__ import annotations

import logging
from pathlib import Path

from valuz_agent.modules.agent_packs.manifest import AgentPackManifest

logger = logging.getLogger(__name__)

# Display order. First role of a pack reads as its lead.
BUILTIN_PACK_IDS: tuple[str, ...] = ("content", "investment", "product")

# The teams onboarding recommends. A subset (here: all) of the built-in packs.
ONBOARDING_PACK_IDS: tuple[str, ...] = ("content", "investment", "product")


def _packs_root() -> Path:
    """``backend/valuz_agent/resources/agent_packs/``."""
    return Path(__file__).resolve().parent.parent.parent / "resources" / "agent_packs"


def load_builtin_pack(pack_id: str) -> AgentPackManifest | None:
    """Load and validate one built-in pack by id; ``None`` if absent."""
    path = _packs_root() / pack_id / "manifest.json"
    if not path.is_file():
        return None
    try:
        return AgentPackManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("failed to load built-in agent pack: %s", pack_id)
        return None


def load_builtin_packs(ids: tuple[str, ...] = BUILTIN_PACK_IDS) -> list[AgentPackManifest]:
    """Load every built-in pack in ``ids`` (default: all, in display order)."""
    out: list[AgentPackManifest] = []
    for pid in ids:
        manifest = load_builtin_pack(pid)
        if manifest is not None:
            out.append(manifest)
    return out
