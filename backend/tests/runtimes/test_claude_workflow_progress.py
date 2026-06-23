"""The terminal workflow snapshot must match the live snapshot's shape.

A Claude dynamic-workflow run streams live progress while it executes, then
writes a rich ``wf_<id>.json`` result file at the end. That file is a *different
shape* from the live snapshots: it has no top-level ``agentsDone``, folds
``workflow_phase`` rows into ``workflowProgress`` next to the ``workflow_agent``
rows, and carries heavy ``script`` / ``logs`` / preview blobs. Emitting it raw
made the terminal frame unreadable to the UI — ``agentsDone`` came through as 0
and the card stayed stuck on "running" after the run had finished.
``_normalize_terminal_state`` folds the result file onto the live keys so the UI
sees one contract; these tests pin that projection.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede `from src.*`
from __future__ import annotations

import kernel  # noqa: F401  (puts kernel ``src`` on the import path)

from src.runtimes.claude_agent.runtime import ClaudeAgentRuntime


_STATE_PATH = "/tmp/wf/workflows/wf_abc.json"


def _raw_result_file(status: str | None) -> dict:
    """A trimmed copy of a real ``wf_<id>.json``: phase rows mixed into
    ``workflowProgress``, no ``agentsDone``, one agent still ``progress``."""
    raw: dict = {
        "runId": "wf_abc",
        "workflowName": "deep-research",
        "agentCount": 3,
        "script": "export const meta = {...}",  # heavy field, must be dropped
        "logs": ["a", "b"],
        "result": {"question": "Q?", "summary": "the answer", "findings": []},
        "workflowProgress": [
            {"type": "workflow_phase", "index": 1, "title": "Scope"},
            {"type": "workflow_agent", "agentId": "a1", "state": "done", "label": "scope"},
            {"type": "workflow_agent", "agentId": "a2", "state": "done"},
            {"type": "workflow_agent", "agentId": "a3", "state": "progress"},
        ],
    }
    if status is not None:
        raw["status"] = status
    return raw


def test_normalize_folds_result_file_onto_live_shape():
    out = ClaudeAgentRuntime._normalize_terminal_state(
        _raw_result_file("killed"), "wf_abc", "sum", _STATE_PATH
    )

    # Heavy / phase fields are gone; only the live keys (+ result surface) remain.
    assert set(out) == {
        "runId",
        "workflowName",
        "status",
        "agentCount",
        "agentsDone",
        "workflowProgress",
        "statePath",
        "resultQuestion",
        "resultSummary",
    }
    assert out["runId"] == "wf_abc"
    assert out["workflowName"] == "deep-research"
    # Explicit terminal verb is preserved (the run didn't finish cleanly).
    assert out["status"] == "killed"
    assert out["agentCount"] == 3
    # agentsDone is DERIVED from the agent rows (the file has no such field).
    assert out["agentsDone"] == 2
    # Only ``workflow_agent`` rows survive — the phase row is dropped.
    assert [a["agentId"] for a in out["workflowProgress"]] == ["a1", "a2", "a3"]
    assert all(a["type"] == "workflow_agent" for a in out["workflowProgress"])


def test_normalize_coerces_missing_status_to_completed():
    # No status stamped → the run is over by the time the file exists, so it's
    # ``completed`` (otherwise the UI can't tell the run finished).
    out = ClaudeAgentRuntime._normalize_terminal_state(
        _raw_result_file(None), "wf_abc", "sum", _STATE_PATH
    )
    assert out["status"] == "completed"
    assert out["agentsDone"] == 2


def test_normalize_coerces_stale_running_status_to_completed():
    # A result file that still says "running" is stale — the file's existence
    # means the run ended. Coerce so the card can't stay stuck pulsing.
    out = ClaudeAgentRuntime._normalize_terminal_state(
        _raw_result_file("running"), "wf_abc", "sum", _STATE_PATH
    )
    assert out["status"] == "completed"


def test_normalize_preserves_completed_status_and_counts_done():
    raw = _raw_result_file("completed")
    for a in raw["workflowProgress"]:
        if a.get("type") == "workflow_agent":
            a["state"] = "done"
    out = ClaudeAgentRuntime._normalize_terminal_state(raw, "wf_abc", "sum", _STATE_PATH)
    assert out["status"] == "completed"
    assert out["agentsDone"] == 3
    assert out["agentCount"] == 3


def test_normalize_derives_agent_count_when_absent():
    raw = _raw_result_file("completed")
    del raw["agentCount"]
    out = ClaudeAgentRuntime._normalize_terminal_state(raw, "wf_abc", "sum", _STATE_PATH)
    # Falls back to the number of agent rows when the file omits agentCount.
    assert out["agentCount"] == 3


def test_normalize_tolerates_missing_progress_array():
    out = ClaudeAgentRuntime._normalize_terminal_state(
        {"runId": "wf_x", "status": "completed"}, "wf_x", "sum", _STATE_PATH
    )
    assert out["agentsDone"] == 0
    assert out["agentCount"] == 0
    assert out["workflowProgress"] == []


def test_normalize_surfaces_result_and_state_path():
    # The finished card must not be a dead end: it carries the run's returned
    # question/summary inline plus a link to the full result file.
    out = ClaudeAgentRuntime._normalize_terminal_state(
        _raw_result_file("completed"), "wf_abc", "sum", _STATE_PATH
    )
    assert out["resultQuestion"] == "Q?"
    assert out["resultSummary"] == "the answer"
    assert out["statePath"] == _STATE_PATH


def test_normalize_nulls_result_fields_when_run_has_no_result():
    # A killed/aborted run has ``result: null`` — surface nulls (not a crash),
    # but still expose ``statePath`` so the user can inspect the raw file.
    raw = _raw_result_file("killed")
    raw["result"] = None
    out = ClaudeAgentRuntime._normalize_terminal_state(raw, "wf_abc", "sum", _STATE_PATH)
    assert out["resultQuestion"] is None
    assert out["resultSummary"] is None
    assert out["statePath"] == _STATE_PATH
