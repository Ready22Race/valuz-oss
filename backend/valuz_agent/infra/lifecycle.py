"""Process draining signal — flipped once at the start of shutdown.

A single, dependency-free boolean the shutdown sequence sets BEFORE it tears
anything down. Long-lived background tasks (the task **actor loops**) read it to:

  1. stop starting NEW turns, and
  2. skip their post-turn ``_finalize_actor``.

…so in-flight sessions are left ``running`` / their tasks ``active`` for boot
recovery (``recover_running_sessions`` / ``recover_active_tasks``) to resume —
instead of racing the teardown of the kernel store + host DB (which would both
spam ``Dependencies not initialized`` tracebacks AND wrongly mark the
task/member terminal, the opposite of what recovery wants).

Plain module-level state, no asyncio primitives: it's read from sync code paths
and is single-process; a bool flip + read is all that's needed.
"""

from __future__ import annotations

_draining = False


def set_draining() -> None:
    """Mark the process as shutting down. Idempotent."""
    global _draining
    _draining = True


def is_draining() -> bool:
    """True once shutdown has begun (see module docstring)."""
    return _draining


def reset_draining() -> None:
    """Test-only: clear the flag so suites don't leak shutdown state."""
    global _draining
    _draining = False
