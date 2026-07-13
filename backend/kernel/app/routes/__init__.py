"""Kernel route package — shared HTTP path-prefix configuration.

Every ``app.routes.*`` router mounts under ``KERNEL_API_PREFIX`` + its own
``/v1/...`` segment (e.g. ``{KERNEL_API_PREFIX}/v1/sessions``). Read from the
environment ONCE, at this package's first import — Python only executes a
module body once per process, and each router's path is frozen the moment
``APIRouter(prefix=...)`` (or, for ``app.routes.messages``, each
``@router.get(...)`` decorator) runs at import time. An embedder that wants a
different prefix MUST set ``KERNEL_API_PREFIX`` in the process environment
before importing any ``app.routes.*`` module.

Defaults to ``/kernel`` (ADR-013): the kernel is maintained in-tree (it was
copied in from Agent Harness V5 at repo creation and has evolved here ever
since — there is no upstream to stay compatible with), so the default IS the
value every Valuz deployment runs. The env knob remains for embedders that
need a different mount.
"""

from __future__ import annotations

import os

KERNEL_API_PREFIX = os.environ.get("KERNEL_API_PREFIX", "/kernel")

__all__ = ["KERNEL_API_PREFIX"]
