"""Chat-side binding commands — flow B of the group ↔ project binding.

The lightweight complement to the Valuz project page (flow A) and the guided
card (flow C): someone already typing in the group can bind, inspect, or unbind
without leaving it. Deliberately a tiny, explicit grammar rather than intent
inference — a command that binds the group's work to the wrong project by
guessing would be worse than not understanding at all.

See docs/design/channel-project-binding-and-default-lead.md §5.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class ChannelCommandKind(StrEnum):
    BIND_PROJECT = "bind_project"
    SHOW_PROJECT = "show_project"
    UNBIND_PROJECT = "unbind_project"


@dataclass(frozen=True, slots=True)
class ChannelCommand:
    kind: ChannelCommandKind
    argument: str | None = None


# ``绑定项目 研究`` / ``绑定到 研究`` / ``bind project research``
_BIND_RE = re.compile(
    r"^(?:绑定(?:到)?(?:项目)?|bind(?:\s+to)?(?:\s+project)?)\s*[:：]?\s*(?P<name>.+)$",
    re.IGNORECASE,
)
# ``当前项目`` / ``现在是哪个项目`` / ``which project``
_SHOW_RE = re.compile(
    r"^(?:当前项目|现在(?:是)?哪个项目|哪个项目|which\s+project|current\s+project)[?？]?$",
    re.IGNORECASE,
)
# ``解绑`` / ``解除绑定`` / ``unbind``
_UNBIND_RE = re.compile(
    r"^(?:解绑(?:项目)?|解除(?:项目)?绑定|unbind(?:\s+project)?)$",
    re.IGNORECASE,
)


def parse_channel_command(text: str) -> ChannelCommand | None:
    """Parse a binding command, or ``None`` for ordinary conversation.

    Only a message that is *entirely* a command counts: a project brief that
    happens to mention "绑定项目" in passing must reach the agent as content,
    not silently rebind the group.
    """
    normalized = " ".join((text or "").strip().split())
    if not normalized:
        return None
    # Tolerate a leading slash so ``/绑定项目 X`` reads naturally to anyone who
    # expects IM bots to take slash commands.
    normalized = normalized.lstrip("/").strip()

    if _SHOW_RE.match(normalized):
        return ChannelCommand(ChannelCommandKind.SHOW_PROJECT)
    if _UNBIND_RE.match(normalized):
        return ChannelCommand(ChannelCommandKind.UNBIND_PROJECT)
    match = _BIND_RE.match(normalized)
    if match:
        name = match.group("name").strip().strip("：:")
        if name:
            return ChannelCommand(ChannelCommandKind.BIND_PROJECT, argument=name)
    return None


__all__ = ["ChannelCommand", "ChannelCommandKind", "parse_channel_command"]
