"""Actor loops drain cleanly at shutdown.

治本 for the restart race: once the process starts shutting down
(``infra.lifecycle.set_draining``), the long-lived task **actor loops** must
stop starting new turns AND skip their ``_finalize_actor`` — leaving in-flight
sessions ``running`` / tasks ``active`` for boot recovery to resume, instead of
racing the teardown of the kernel store + host DB (which spammed
``Dependencies not initialized`` and would wrongly mark tasks terminal).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from valuz_agent.infra import lifecycle
from valuz_agent.modules.sessions.turn_driver import run_session_to_idle
from valuz_agent.modules.tasks import actor_runner
from valuz_agent.modules.tasks.actor_runner import ActorRunner

LOCAL_USER_ID = "local-test-owner"


@pytest.fixture(autouse=True)
def _clear_draining():
    lifecycle.reset_draining()
    yield
    lifecycle.reset_draining()


def _as_async(fn: Any) -> Any:
    async def _f(*a: Any, **k: Any) -> Any:
        return fn(*a, **k)

    return _f


class _Bus:
    def publish(self, *a: Any, **k: Any) -> None:
        pass


# ── infra.lifecycle ──────────────────────────────────────────────────────


def test_draining_flag_roundtrip() -> None:
    assert lifecycle.is_draining() is False
    lifecycle.set_draining()
    assert lifecycle.is_draining() is True
    lifecycle.reset_draining()
    assert lifecycle.is_draining() is False


# ── ActorRunner.run_actor_loop ───────────────────────────────────────────


def test_actor_loop_draining_skips_turn_and_finalize() -> None:
    calls: list[str] = []

    class _Host:
        async def _run_turn_with_sink(self, sid: str, content: str, user_id: str | None = None) -> str:
            calls.append("turn")
            return "idle"

        async def _finalize_actor(self, **k: Any) -> None:
            calls.append("finalize")

        async def _notify_lead_member_idle(self, sid: str, status: str, user_id: str | None = None) -> None:
            pass

        async def _lead_idle_with_no_pending(self, t: str, p: str, user_id: str | None = None) -> bool:
            return True

    runner = ActorRunner(_Host())
    lifecycle.set_draining()
    asyncio.run(
        runner.run_actor_loop(
            session_id="s1",
            initial_prompt="hi",
            role="lead",
            task_id="t1",
            project_id="p1",
            user_id=LOCAL_USER_ID,
        )
    )

    # Draining: the loop breaks at the top — no turn ran, and the whole
    # finalize was skipped (session left for boot recovery).
    assert "turn" not in calls
    assert "finalize" not in calls


def test_actor_loop_runs_normally_when_not_draining() -> None:
    calls: list[str] = []

    class _Host:
        async def _run_turn_with_sink(self, sid: str, content: str, user_id: str | None = None) -> str:
            calls.append("turn")
            return "terminated"  # terminal → loop exits after one turn

        async def _finalize_actor(self, **k: Any) -> None:
            calls.append("finalize")

        async def _notify_lead_member_idle(self, sid: str, status: str, user_id: str | None = None) -> None:
            pass

        async def _lead_idle_with_no_pending(self, t: str, p: str, user_id: str | None = None) -> bool:
            return True

    runner = ActorRunner(_Host())
    asyncio.run(
        runner.run_actor_loop(
            session_id="s2",
            initial_prompt="hi",
            role="lead",
            task_id="t2",
            project_id="p2",
            user_id=LOCAL_USER_ID,
        )
    )

    # Not draining: the turn runs and finalize happens as usual.
    assert calls == ["turn", "finalize"]


# ── run_session_to_idle ──────────────────────────────────────────────────


def test_run_session_to_idle_draining_skips_finalize(monkeypatch: pytest.MonkeyPatch) -> None:
    finalize_calls: list[str] = []
    sess = SimpleNamespace(status="idle", metadata={})
    monkeypatch.setattr(actor_runner.kernel_client, "get_session", _as_async(lambda *_: sess))
    monkeypatch.setattr(
        actor_runner.kernel_client,
        "run_turn",
        _as_async(lambda *a, **k: SimpleNamespace(id="m", input_tokens=None, output_tokens=None)),
    )
    import valuz_agent.modules.sessions.run_orchestrator as run_orch

    monkeypatch.setattr(
        run_orch, "_finalize_session", _as_async(lambda *a, **k: finalize_calls.append("f"))
    )

    lifecycle.set_draining()
    asyncio.run(run_session_to_idle("s3", "hi", _Bus(), user_id=LOCAL_USER_ID))

    assert finalize_calls == []  # finalize skipped while draining
