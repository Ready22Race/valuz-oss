"""Memory types (memory-system-design §4)."""

from __future__ import annotations

from typing import Literal

# Three write targets, 1:1 with the three flat §-delimited files (design §3/§4):
#   user    -> <memories>/USER.md                 (global: who the user is)
#   global  -> <memories>/MEMORY.md               (global: cross-project notes/lessons)
#   project -> <memories>/projects/<id>/MEMORY.md (this project)
Target = Literal["user", "global", "project"]
TARGETS: tuple[Target, ...] = ("user", "global", "project")

# Who wrote it. "auto" = background extractor; "agent" = foreground tool call;
# "user" = explicit user-driven write. A tag only — every source goes through the
# same write pipeline (design §5). Not persisted per-entry in P0 (flat files have
# no per-entry metadata); used for logging/gating. Per-entry markers arrive in P2.
Source = Literal["agent", "auto", "user"]

# Entry delimiter — a sequence that essentially never appears in prose, so an
# entry may itself be multi-line (design §3).
ENTRY_DELIMITER = "\n§\n"

# Hard char limits per target (chars, not tokens — model-independent). Design §4.
CHAR_LIMITS: dict[Target, int] = {
    "user": 1500,
    "global": 2500,
    "project": 4000,
}

__all__ = ["Target", "TARGETS", "Source", "ENTRY_DELIMITER", "CHAR_LIMITS"]
