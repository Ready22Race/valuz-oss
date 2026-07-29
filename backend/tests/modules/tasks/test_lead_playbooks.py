"""The two lead playbooks are one body with two headers — keep them that way.

They used to be hand-maintained copies that were ~60% identical, and they
drifted apart in exactly the way duplicated prose does: each kept guidance the
other lacked. The committed copy taught ``dispatch(key)`` when the handler
requires ``subtask_key`` and hard-fails without it; the kickoff copy never
explained ``<user-instruction>`` or ``expected_version`` even though a
kickoff-path lead receives both. Those were live defects — the model acts on
these strings.
"""

from __future__ import annotations

import pytest

from valuz_agent.adapters.agent_resolver import (
    COMMITTED_LEAD_PLAYBOOK,
    DISPATCH_PLAYBOOK,
)

BOTH = pytest.mark.parametrize(
    "playbook",
    [DISPATCH_PLAYBOOK, COMMITTED_LEAD_PLAYBOOK],
    ids=["kickoff", "committed"],
)


@BOTH
@pytest.mark.parametrize(
    "must_contain",
    [
        # The real parameter name — the handler rejects the call without it.
        "dispatch(subtask_key=...)",
        "review_subtask(subtask_key=...",
        # Guidance that lived in only ONE copy before the merge:
        "expected_version",  # was committed-only
        "PLAN_VERSION_CONFLICT",  # was committed-only
        "<user-instruction source=\"chat\">",  # was committed-only
        "in_review/rework/paused",  # was kickoff-only (and missed `paused`)
        "BLOCKS you for minutes",  # was kickoff-only
        "<system-recovery>",
        # The rework branch the tool result actually reports.
        "delivered_to_live_member",
    ],
)
def test_both_playbooks_carry_the_shared_protocol(playbook: str, must_contain: str) -> None:
    assert must_contain in playbook


@BOTH
@pytest.mark.parametrize(
    "must_not_contain",
    [
        # Argument names the handlers reject (the drift that shipped).
        "dispatch(key)",
        "review_subtask(key,",
        # A removal primitive modify_plan does not have — following it leaves
        # the lead with a node it cannot clear and a finish_task it cannot pass.
        "remove them",
    ],
)
def test_neither_playbook_teaches_a_refused_call(playbook: str, must_not_contain: str) -> None:
    assert must_not_contain not in playbook


def test_only_step_one_differs() -> None:
    """The variants exist for ONE reason: plan first vs read the committed
    plan. Everything from step 2 on is the shared body — if this fails, the
    copies are drifting again."""
    tail_marker = "\n2. DISPATCH INDEPENDENT SUBTASKS IN PARALLEL."
    assert DISPATCH_PLAYBOOK[DISPATCH_PLAYBOOK.index(tail_marker) :] == (
        COMMITTED_LEAD_PLAYBOOK[COMMITTED_LEAD_PLAYBOOK.index(tail_marker) :]
    )
    assert "1. PLAN FIRST." in DISPATCH_PLAYBOOK
    assert "1. READ THE PLAN." in COMMITTED_LEAD_PLAYBOOK
    assert "DO NOT call plan_task" in COMMITTED_LEAD_PLAYBOOK
