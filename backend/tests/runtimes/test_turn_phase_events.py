"""``turn_phase`` emission — the per-runtime latency markers.

Pins the event shape each runtime pushes through its sink and, for codex,
that ``_ensure_thread`` labels the start-vs-resume mode correctly — the
marker exists precisely to answer "was this turn's thread new or resumed"
during latency triage.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from src.core.agent_config import AgentConfig
from src.core.types import Session
from src.runtimes.claude_agent.runtime import ClaudeAgentRuntime
from src.runtimes.codex.runtime import CodexRuntime
from src.runtimes.deepagents.runtime import DeepAgentsRuntime


class _FakeSink:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def emit(self, event: Any) -> None:
        self.events.append(event)


def _session(runtime_session_id: str | None = None) -> Session:
    return Session(
        id="s1",
        agent_config=AgentConfig(id="a", name="a"),
        cwd="/tmp",
        runtime_provider="codex",
        runtime_session_id=runtime_session_id,
    )


async def test_emit_turn_phase_shape_per_runtime() -> None:
    for cls in (CodexRuntime, ClaudeAgentRuntime, DeepAgentsRuntime):
        rt = object.__new__(cls)
        rt.event_sink = _FakeSink()
        await rt._emit_turn_phase("dispatch", duration_ms=7)
        (event,) = rt.event_sink.events
        assert event.type == "turn_phase", cls.__name__
        assert event.data == {"phase": "dispatch", "duration_ms": 7}, cls.__name__


class _FakeThreadClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def thread_start(self, params: Any) -> Any:
        self.calls.append("start")
        return SimpleNamespace(thread=SimpleNamespace(id="thread-new"))

    async def thread_resume(self, thread_id: str, params: Any) -> Any:
        self.calls.append("resume")
        return SimpleNamespace(thread=SimpleNamespace(id=thread_id))


def _codex_rt() -> CodexRuntime:
    rt = object.__new__(CodexRuntime)
    rt.event_sink = _FakeSink()
    rt._thread = None
    rt._codex = SimpleNamespace(_client=_FakeThreadClient())
    rt.model = "deepseek-v4-flash"
    rt.workspace_root = "/tmp"
    return rt


async def test_codex_thread_init_marks_start_mode() -> None:
    rt = _codex_rt()
    await rt._ensure_thread(_session(runtime_session_id=None))
    phases = [e.data for e in rt.event_sink.events if e.type == "turn_phase"]
    assert len(phases) == 1
    assert phases[0]["phase"] == "thread_init"
    assert phases[0]["mode"] == "start"
    assert isinstance(phases[0]["duration_ms"], int)


async def test_codex_thread_init_marks_resume_mode() -> None:
    rt = _codex_rt()
    await rt._ensure_thread(_session(runtime_session_id="thread-old"))
    phases = [e.data for e in rt.event_sink.events if e.type == "turn_phase"]
    assert len(phases) == 1
    assert phases[0]["phase"] == "thread_init"
    assert phases[0]["mode"] == "resume"


async def test_codex_thread_ensure_is_idempotent_no_repeat_marker() -> None:
    # A warm turn (thread already established) must not re-emit thread_init.
    rt = _codex_rt()
    session = _session(runtime_session_id=None)
    await rt._ensure_thread(session)
    await rt._ensure_thread(session)
    phases = [e for e in rt.event_sink.events if e.type == "turn_phase"]
    assert len(phases) == 1
