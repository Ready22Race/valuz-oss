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

from src.runtimes.interruption import is_runtime_interruption


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
