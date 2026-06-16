"""`_execute_task_kickoff` run-row bookkeeping.

Two regressions guarded here:

  * **duration_ms** — ``started_at`` must be stamped BEFORE the kickoff call so
    the run's duration reflects the real kickoff cost. It used to be set right
    next to ``completed_at`` (both after kickoff), collapsing every task
    automation's duration to ~0ms in the activity log.
  * **result_summary** — must be a localized, human-readable line built from the
    task title, NOT the raw ``f"Task kicked off: {task.id}"`` (an opaque id the
    user can't act on; deep-linking already rides ``session_id``).
"""

from __future__ import annotations

import itertools
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from valuz_agent.modules.automations.in_process_runner import InProcessAutomationRunner


@asynccontextmanager
async def _fake_uow(*args, **kwargs):
    yield Mock()


@pytest.mark.asyncio
async def test_task_kickoff_stamps_duration_and_localized_summary() -> None:
    runner = InProcessAutomationRunner()
    runner._triggers = Mock()
    runner._triggers.next_fire_at = Mock(return_value=9_999)

    ds = Mock()
    ds.replace_run = AsyncMock()
    ds.update_automation = AsyncMock()
    ds.trim_runs = AsyncMock()

    row = SimpleNamespace(
        project_id="proj-1",
        agent_slug="researcher",
        name="my-automation",
        user_id="u1",
        status="enabled",
        last_run_at=None,
        next_run_at=None,
        updated_at=0,
    )
    run = SimpleNamespace(
        status="queued",
        started_at=None,
        completed_at=None,
        duration_ms=None,
        result_summary=None,
        session_id=None,
        triggered_at=500,
    )

    fake_task = SimpleNamespace(id="task-abc-123")
    lead_run = SimpleNamespace(kind="lead", session_id="lead-sess-1")
    ts_ds = Mock()
    ts_ds.list_runs = AsyncMock(return_value=[lead_run])

    prompt = "研究今日A股热门板块与资金情况"

    with (
        patch(
            "valuz_agent.modules.automations.in_process_runner.now_ms",
            side_effect=itertools.count(1000, 100),
        ),
        patch(
            "valuz_agent.modules.automations.in_process_runner.require_current_user_id",
            return_value="u1",
        ),
        patch(
            "valuz_agent.modules.tasks.orchestrator.task_orchestrator.kickoff",
            new=AsyncMock(return_value=fake_task),
        ),
        patch(
            "valuz_agent.modules.tasks.datastore.TaskSessionDatastore",
            return_value=ts_ds,
        ),
        patch("valuz_agent.infra.db.async_unit_of_work", _fake_uow),
    ):
        await runner._execute_task_kickoff(
            ds=ds,
            row=row,
            run=run,
            run_id="run-1",
            automation_id="auto-1",
            rendered_prompt=prompt,
        )

    # Duration is real (started stamped before kickoff → completed after).
    assert run.status == "success"
    assert run.started_at is not None and run.completed_at is not None
    assert run.started_at < run.completed_at
    assert run.duration_ms is not None and run.duration_ms > 0

    # Summary is the localized title line — not the raw task id.
    assert run.result_summary
    assert "task-abc-123" not in run.result_summary
    assert not run.result_summary.startswith("Task kicked off:")
    assert prompt in run.result_summary  # title == rendered prompt (< 60 chars)

    # Lead session deep-link still wired.
    assert run.session_id == "lead-sess-1"
