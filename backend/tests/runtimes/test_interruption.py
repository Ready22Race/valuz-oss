"""``is_runtime_interruption`` — classify a mid-turn exception as a runtime
process/transport death (resumable ``interrupted``) vs a real task failure.

The runtimes call this in their ``except Exception`` block: a True verdict
stamps a resumable ``interrupted`` stop_reason and suppresses ``session_error``;
a False verdict keeps the terminal ``execution_error`` + error card. See
``src.runtimes.interruption`` for the full rationale (graceful shutdown must not
behave worse than a hard kill).
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from openai_codex import TransportClosedError

from src.runtimes.interruption import (
    describe_exception,
    is_runtime_interruption,
    iter_leaf_exceptions,
)


def test_codex_transport_closed_is_interruption() -> None:
    exc = TransportClosedError("Codex process closed stdout. stderr_tail=...")
    assert is_runtime_interruption(exc) is True


def test_broken_pipe_is_interruption() -> None:
    # Writing to the dead subprocess's stdin after it exits.
    assert is_runtime_interruption(BrokenPipeError(32, "Broken pipe")) is True


def test_connection_and_reset_errors_are_interruptions() -> None:
    assert is_runtime_interruption(ConnectionError("transport gone")) is True
    assert is_runtime_interruption(ConnectionResetError("reset")) is True


def test_eof_is_interruption() -> None:
    # Reading from a closed transport.
    assert is_runtime_interruption(EOFError()) is True


def test_real_task_failures_are_not_interruptions() -> None:
    # Genuine errors must stay terminal (execution_error + session_error card).
    assert is_runtime_interruption(RuntimeError("provider exploded")) is False
    assert is_runtime_interruption(ValueError("bad arg")) is False
    assert is_runtime_interruption(KeyError("missing")) is False


# --- ExceptionGroup unwrapping (the SDK transport/MCP task-group wrap) -------
#
# The SDKs run their transport / MCP client over an anyio task group, so a
# mid-turn transport death surfaces WRAPPED as ``ExceptionGroup`` whose ``str``
# is the opaque "unhandled errors in a TaskGroup (1 sub-exception)". Before the
# group-aware fix the ``isinstance`` checks missed the wrap → the subtask was
# mis-stamped a hard ``execution_error`` instead of the resumable
# ``interrupted``, which is the intermittent task-mode subtask failure.


def test_taskgroup_wrapped_transport_death_is_interruption() -> None:
    # This is exactly what asyncio.TaskGroup / anyio raise when a child task
    # dies with a transport error: str(group) hides the real BrokenPipeError.
    group = ExceptionGroup("unhandled errors in a TaskGroup", [BrokenPipeError(32, "Broken pipe")])
    assert "unhandled errors in a TaskGroup" in str(group)
    assert is_runtime_interruption(group) is True


def test_nested_taskgroup_transport_death_is_interruption() -> None:
    # Groups nest (a group inside a group) — recursion must reach the leaf.
    inner = ExceptionGroup("inner", [ConnectionResetError("reset")])
    outer = ExceptionGroup("outer", [inner])
    assert is_runtime_interruption(outer) is True


def test_taskgroup_of_real_errors_is_not_interruption() -> None:
    group = ExceptionGroup("boom", [ValueError("bad"), RuntimeError("worse")])
    assert is_runtime_interruption(group) is False


def test_describe_exception_unwraps_to_leaf() -> None:
    group = ExceptionGroup("unhandled errors in a TaskGroup", [BrokenPipeError(32, "Broken pipe")])
    desc = describe_exception(group)
    # The opaque group wording is gone; the real cause is surfaced.
    assert "TaskGroup" not in desc
    assert "BrokenPipeError" in desc
    assert "Broken pipe" in desc


def test_describe_exception_collapses_duplicate_fanout() -> None:
    # Same error raised by N parallel tasks → one line, not N.
    group = ExceptionGroup("g", [ConnectionResetError("reset"), ConnectionResetError("reset")])
    assert describe_exception(group) == "ConnectionResetError: reset"


def test_describe_exception_plain_passthrough() -> None:
    assert describe_exception(ValueError("bad arg")) == "bad arg"


def test_iter_leaf_exceptions_flattens() -> None:
    leaf_a = ValueError("a")
    leaf_b = KeyError("b")
    group = ExceptionGroup("outer", [ExceptionGroup("inner", [leaf_a]), leaf_b])
    assert list(iter_leaf_exceptions(group)) == [leaf_a, leaf_b]
