"""A ``ResultMessage`` must be classified by ``is_error``, not ``subtype`` alone.

The Claude Agent SDK / CLI can return ``subtype="success"`` while still setting
``is_error=True`` — it does this when an API call fails after the CLI exhausted
its own retries (transient 429/500/529, dropped connection on a flaky network).
The HTTP code lands in ``api_error_status`` and detail in ``errors``. Trusting
``subtype`` would surface that failed turn as a clean ``EndTurn`` (the bug); the
runtime must stamp an ``Error`` instead. Several ``ResultMessage`` fields carry
diagnostic detail when the conversation ends on an error, so the message is
composed from whichever are populated.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede `from src.*`
from __future__ import annotations

from types import SimpleNamespace

# Side-effect import: puts the kernel ``src/`` on sys.path before any ``from
# src.*`` below resolves. Mirrors tests/runtimes/test_claude_buffer_size.py.
import kernel  # noqa: F401

from claude_agent_sdk import ResultMessage

from src.core.types import BudgetExhausted, EndTurn, Error
from src.runtimes.claude_agent.runtime import _result_error_message


def _make_runtime():
    """A ``ClaudeAgentRuntime`` with the SDK-touching ``__init__`` bypassed —
    only the attributes the ``ResultMessage`` branch of ``_handle_message``
    reads are set (a no-op async event sink, model, not-cancelled)."""
    from src.runtimes.claude_agent.runtime import ClaudeAgentRuntime

    emitted: list = []

    async def _emit(event) -> None:
        emitted.append(event)

    rt = object.__new__(ClaudeAgentRuntime)
    rt.model = "claude-sonnet-4-6"
    rt.event_sink = SimpleNamespace(emit=_emit)
    rt._cancelled = False
    rt._emitted = emitted  # test handle
    return rt


def _result(subtype: str, *, is_error: bool, **kw) -> ResultMessage:
    return ResultMessage(
        subtype=subtype,
        duration_ms=0,
        duration_api_ms=0,
        is_error=is_error,
        num_turns=1,
        session_id="sess-1",
        **kw,
    )


async def _classify(message: ResultMessage):
    rt = _make_runtime()
    session = SimpleNamespace(status="running", stop_reason=None)
    await rt._handle_message(session, message)
    return session.stop_reason


# -- the core fix: subtype="success" is not enough -------------------------


async def test_clean_success_is_end_turn() -> None:
    reason = await _classify(_result("success", is_error=False, result="done"))
    assert isinstance(reason, EndTurn)


async def test_success_with_is_error_is_api_error() -> None:
    reason = await _classify(
        _result("success", is_error=True, api_error_status=529, result="Overloaded")
    )
    assert isinstance(reason, Error)
    assert reason.category == "api_error"
    assert reason.retry_status == "exhausted"
    assert "529" in reason.message  # HTTP status surfaced for diagnosis


async def test_success_with_is_error_uses_errors_when_no_status() -> None:
    reason = await _classify(
        _result("success", is_error=True, errors=["connection reset", "retry budget spent"])
    )
    assert isinstance(reason, Error)
    assert reason.category == "api_error"
    assert "connection reset" in reason.message


# -- the other branches keep working ----------------------------------------


async def test_error_max_turns_is_budget() -> None:
    reason = await _classify(_result("error_max_turns", is_error=True))
    assert isinstance(reason, BudgetExhausted)
    assert reason.reason == "max_turns"


async def test_error_max_budget_is_budget() -> None:
    reason = await _classify(_result("error_max_budget_usd", is_error=True))
    assert isinstance(reason, BudgetExhausted)
    assert reason.reason == "max_cost"


async def test_error_during_execution_keeps_category() -> None:
    reason = await _classify(_result("error_during_execution", is_error=True, result="boom"))
    assert isinstance(reason, Error)
    assert reason.category == "execution_error"
    assert "boom" in reason.message


async def test_unknown_subtype_falls_through_to_error() -> None:
    reason = await _classify(_result("error_something_new", is_error=True, result="weird"))
    assert isinstance(reason, Error)
    assert reason.category == "error_something_new"
    assert "weird" in reason.message


# -- diagnostic message composition -----------------------------------------


def test_message_combines_status_errors_and_result() -> None:
    msg = _result(
        "success",
        is_error=True,
        api_error_status=500,
        errors=["upstream timeout"],
        result="final text",
    )
    out = _result_error_message(msg)
    assert "HTTP 500" in out
    assert "upstream timeout" in out
    assert "final text" in out


def test_message_falls_back_to_stop_reason_then_generic() -> None:
    # Only the SDK stop_reason string is available.
    only_stop = _result("success", is_error=True, stop_reason="max_tokens")
    assert "max_tokens" in _result_error_message(only_stop)

    # Nothing populated -> generic, never empty.
    empty = _result("success", is_error=True)
    assert _result_error_message(empty) == "agent ended on an error"
