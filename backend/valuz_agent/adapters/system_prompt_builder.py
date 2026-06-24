"""Build a kernel-shaped ``instructions`` string from valuz project context.

The kernel's V5 ClaudeAgentRuntime uses ``SystemPromptPreset`` with a
preset of ``claude_code`` and an optional ``append`` string. Per ADR-008
the runtime now reads that append from ``Session.instructions`` (not
``Agent.instructions``); valuz writes this string into the session at
create time so it stays frozen for the session's lifetime — see
``domains/execution/sessions/service.py:create_session``.

This module is the *only* place in valuz that decides what that string
looks like. Keep it small and deterministic so re-runs (e.g. when the user
edits ``instructions_md`` and a new session is created) produce stable
session rows.
"""

from __future__ import annotations


def build_project_system_prompt(
    *,
    project_name: str,
    instructions_md: str | None,
) -> str:
    """Compose the session's ``instructions`` string from project metadata.

    Returns the project's ``instructions_md`` verbatim (trimmed). Returns
    an empty string when the project has no instructions — the kernel's
    runtime treats an empty append the same as omitting it.

    No ``# Project: <name>`` header is prepended: the kernel writes a
    project ``CLAUDE.md`` with the project name as H1 (see
    ``src.core.workspace.bootstrap_session_workspace``) and the runtime
    surfaces ``cwd`` to the model independently, so a synthetic header
    here would be redundant. It would also create a visible mismatch in
    the frontend session panel, which renders ``session.instructions``
    verbatim and side-by-side with the project's editable
    ``instructions_md`` — users would see different text in two places
    that should be identical.
    """
    del project_name  # kept in signature for API stability; see docstring
    return (instructions_md or "").strip()


def assemble_session_instructions(sections: list[tuple[str, str]]) -> str:
    """Join the session system-prompt blocks, each wrapped in an XML tag.

    ``sections`` is an ordered list of ``(tag, text)``. Empty / whitespace-only
    blocks are skipped; the rest are emitted as ``<tag>\\n{text}\\n</tag>`` and
    joined with blank lines. The tags delineate the distinct kinds of guidance
    that used to be concatenated into one undelimited blob — the agent's own
    instructions, the project's instructions, the task playbook, etc. — so both
    the model and a human reading the session panel can tell them apart. This is
    the single chokepoint for that assembly (chat/project + task paths both call
    it), keeping the structure identical everywhere.
    """
    out: list[str] = []
    for tag, text in sections:
        if text and text.strip():
            out.append(f"<{tag}>\n{text.strip()}\n</{tag}>")
    return "\n\n".join(out)


__all__ = ["assemble_session_instructions", "build_project_system_prompt"]
