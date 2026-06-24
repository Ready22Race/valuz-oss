"""assemble_session_instructions — XML-tagged system-prompt assembly."""

from __future__ import annotations

from valuz_agent.adapters.system_prompt_builder import assemble_session_instructions


def test_wraps_each_nonempty_block_in_its_tag() -> None:
    out = assemble_session_instructions(
        [
            ("agent-instructions", "be a researcher"),
            ("project-instructions", "focus on EVs"),
            ("task-playbook", "draft then commit"),
        ]
    )
    assert out == (
        "<agent-instructions>\nbe a researcher\n</agent-instructions>\n\n"
        "<project-instructions>\nfocus on EVs\n</project-instructions>\n\n"
        "<task-playbook>\ndraft then commit\n</task-playbook>"
    )


def test_skips_empty_and_whitespace_blocks() -> None:
    out = assemble_session_instructions(
        [
            ("agent-instructions", "do the thing"),
            ("project-instructions", ""),
            ("task-playbook", "   "),
        ]
    )
    # Only the non-empty block survives — no stray empty tags.
    assert out == "<agent-instructions>\ndo the thing\n</agent-instructions>"


def test_empty_when_all_blank() -> None:
    assert assemble_session_instructions([("a", ""), ("b", None)]) == ""  # type: ignore[list-item]
