"""Classify a mid-turn exception as a *runtime interruption* vs a real failure.

When the host gracefully stops (SIGTERM), it tears down the agent subprocess
while a turn may still be in flight. The SDK then raises a transport/process
death rather than a task error:

  * **codex** — ``TransportClosedError`` ("Codex process closed stdout"), and a
    follow-up write to the dead stdin surfaces as ``BrokenPipeError``.
  * **socket/stdio transports** generally — ``ConnectionError`` (which
    ``BrokenPipeError`` / ``ConnectionResetError`` subclass) or ``EOFError``.

These are NOT task failures. A runtime that stamps them ``execution_error`` +
emits ``session_error`` makes graceful shutdown behave *worse* than a hard
kill: a hard kill leaves the session row ``running`` for ``scan_orphan_runs`` to
flip to the resumable ``host_restart`` category, whereas a graceful stop would
mark the member terminal and recovery would archive it.

Classifying these as a resumable ``interrupted`` stop_reason (and suppressing
the scary ``session_error``) lets boot recovery re-drive the turn exactly like
the hard-kill path. ``RESUME_RETRY_CAP`` in recovery is the backstop for a
subprocess that dies on every attempt.
"""

from __future__ import annotations


def is_runtime_interruption(exc: BaseException) -> bool:
    """True when ``exc`` is the agent subprocess going away mid-turn.

    Detects the transport/process-death exceptions raised when a runtime's
    child process is torn down under it — not genuine task failures.
    """
    if isinstance(exc, (BrokenPipeError, ConnectionError, EOFError)):
        return True
    try:
        from openai_codex import TransportClosedError
    except Exception:
        return False
    return isinstance(exc, TransportClosedError)
