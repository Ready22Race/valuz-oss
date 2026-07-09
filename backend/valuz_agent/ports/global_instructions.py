"""Optional override for deployment-wide global session instructions.

By default an agent-bound session's system prompt starts with the agent's own
persona instructions (``assemble_session_instructions`` in the sessions
service). A deployment that needs a platform-level preamble ahead of every
agent — e.g. a commercial overlay injecting org policy or compliance guidance —
binds this port; the returned text is prepended as the first prompt section.

OSS binds no override (``None``); sessions carry only agent/project-level
instructions. An overlay binds it at startup via ``ext.global_instructions``.
"""

from __future__ import annotations

from typing import Protocol


class GlobalInstructionsPort(Protocol):
    async def global_instructions(self) -> str | None:
        """Deployment-wide instructions prepended to every agent-bound
        session's system prompt, or ``None``/empty to add nothing."""
        ...
