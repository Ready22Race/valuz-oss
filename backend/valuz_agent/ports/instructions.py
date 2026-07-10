"""Optional deployment-level instruction extensions for session prompts.

An **aggregate** port: each method contributes one kind of deployment-owned
instruction text to session-prompt assembly. Today it carries one —
``global_instructions``, a platform-wide preamble prepended as the FIRST
prompt section (``<global-instructions>``) of every session — agent-bound
chat/project, raw/no-agent quick chat, and task lead/member. Future
instruction kinds (e.g.
per-role or compliance addenda) extend this same Protocol rather than adding
new ``ext`` attributes.

OSS binds no override (``None``); sessions carry only agent/project-level
instructions. An overlay binds a provider at startup via ``ext.instructions``.

Consumption sites (all go through ``global_instructions_preamble`` below):
- ``modules/sessions/service._create_agent_bound_session`` (chat/project)
- ``modules/sessions/service.create_session`` raw/no-agent branch (quick chat,
  skill-creator, agent-less scheduled runs)
- ``adapters/agent_resolver.build_member_session`` (task lead + members)
"""

from __future__ import annotations

from typing import Protocol


class InstructionsPort(Protocol):
    async def global_instructions(self) -> str | None:
        """Deployment-wide instructions prepended to every session's system
        prompt, or ``None``/empty to add nothing."""
        ...


async def global_instructions_preamble() -> str:
    """Resolve the deployment-wide preamble through the seam.

    Empty when OSS binds no override or the provider returns ``None``/empty —
    the caller passes it straight to ``assemble_session_instructions``, which
    skips empty sections, so the OSS prompt is unchanged.
    """
    from valuz_agent.ports.extensions import ext

    override = ext.instructions
    if override is None:
        return ""
    return await override.global_instructions() or ""
