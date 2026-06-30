"""Task turn primitives re-stamp the always-on MCP token before ``run_turn``.

Regression for "a re-launched task lead has no orchestration tools": the
in-process MCP token (``settings.internal_mcp_token``) rotates per process, so a
lead/member session re-driven after a backend restart carries a *stale*
``X-Valuz-Internal`` in its persisted ``mcp_servers``. The in-process gate then
403s and the runtime parks the ``harness`` server in needsAuth, hiding dispatch
/ review_subtask / finish_task / await_members / send / get_plan. The chat path
re-stamped in ``send_message``; the task/actor path didn't. Both
``run_session_to_idle`` and ``ActorRunner._run_turn_with_sink`` must now
re-stamp first.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from valuz_agent.modules.tasks import actor_runner


def _as_async(fn: Any) -> Any:
    async def _f(*a: Any, **k: Any) -> Any:
        return fn(*a, **k)

    return _f


class _Bus:
    def publish(self, *a: Any, **k: Any) -> None:  # event-bus stub
        pass


# ── _restamp_always_on_mcp ──────────────────────────────────────────────


def test_restamp_calls_capabilities_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    import valuz_agent.modules.sessions.capabilities as caps

    monkeypatch.setattr(
        caps, "refresh_always_on_mcp_for_session", _as_async(lambda sid: seen.append(sid))
    )
    asyncio.run(actor_runner._restamp_always_on_mcp("sess-1"))
    assert seen == ["sess-1"]


def test_restamp_swallows_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    import valuz_agent.modules.sessions.capabilities as caps

    async def _boom(_sid: str) -> bool:
        raise RuntimeError("kernel down")

    monkeypatch.setattr(caps, "refresh_always_on_mcp_for_session", _boom)
    # Must not raise — a re-stamp failure can never block the turn.
    asyncio.run(actor_runner._restamp_always_on_mcp("sess-1"))


# ── run_session_to_idle ─────────────────────────────────────────────────


def test_run_session_to_idle_restamps_before_run_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []
    monkeypatch.setattr(actor_runner, "get_current_user_id", lambda: "owner-1")
    monkeypatch.setattr(
        actor_runner, "_restamp_always_on_mcp", _as_async(lambda _sid: order.append("restamp"))
    )
    sess = SimpleNamespace(status="idle", metadata={"valuz": {"run_kind": "lead"}})
    monkeypatch.setattr(actor_runner.kernel_client, "get_session", _as_async(lambda *_: sess))

    async def _run_turn(*a: Any, **k: Any) -> Any:
        order.append("run_turn")
        return SimpleNamespace(id="m1", input_tokens=None, output_tokens=None)

    monkeypatch.setattr(actor_runner.kernel_client, "run_turn", _run_turn)
    # finalize hits the DB — stub it out (the primitive logs+continues anyway).
    import valuz_agent.modules.sessions.run_orchestrator as run_orch

    monkeypatch.setattr(run_orch, "_finalize_session", _as_async(lambda *a, **k: None))

    asyncio.run(actor_runner.run_session_to_idle("sess-1", "hi", _Bus()))

    assert "restamp" in order and "run_turn" in order
    assert order.index("restamp") < order.index("run_turn")


# ── ActorRunner._run_turn_with_sink ─────────────────────────────────────


def test_actor_loop_turn_restamps_before_run_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []
    monkeypatch.setattr(actor_runner, "get_current_user_id", lambda: "owner-1")
    monkeypatch.setattr(
        actor_runner, "_restamp_always_on_mcp", _as_async(lambda _sid: order.append("restamp"))
    )

    async def _run_turn(*a: Any, **k: Any) -> Any:
        order.append("run_turn")

    monkeypatch.setattr(actor_runner.kernel_client, "run_turn", _run_turn)
    monkeypatch.setattr(
        actor_runner.kernel_client,
        "get_session",
        _as_async(lambda *_: SimpleNamespace(status="idle")),
    )

    runner = actor_runner.ActorRunner()
    status = asyncio.run(runner._run_turn_with_sink("sess-1", "hi"))

    assert status == "idle"
    assert order == ["restamp", "run_turn"]
