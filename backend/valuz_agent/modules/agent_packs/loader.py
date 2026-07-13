"""Load the built-in Agent Packs shipped under ``resources/agent_packs/``.

Built-in packs are Valuz official Agent Teams. ``BUILTIN_PACK_IDS`` is the
Marketplace display order. Onboarding can keep a smaller, more stable subset
via ``ONBOARDING_PACK_IDS``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from valuz_agent.modules.agent_packs.manifest import AgentPackManifest

logger = logging.getLogger(__name__)

# Marketplace display order. First role of a pack reads as its lead.
BUILTIN_PACK_IDS: tuple[str, ...] = (
    "product-strategy",
    "design-prototype",
    "development-engineering",
    "qa-testing",
    "investment",
    "supply-chain-tracking",
    "competitive-intelligence",
    "content-growth",
    "campaign-event",
    "content",
    "short-video-growth",
    "contract-review",
    "compliance-review",
    "academic-research",
    "training-program",
    "recruiting-evaluation",
    "chinese-metaphysics",
    "health-report",
    "tarot-astrology",
)

# The teams onboarding recommends. Keep this aligned with the Marketplace's
# visible task-oriented teams; the legacy broad ``product`` pack stays on disk
# only for compatibility.
ONBOARDING_PACK_IDS: tuple[str, ...] = ("content", "investment", "development-engineering")


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
