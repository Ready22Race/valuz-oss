"""Runtime availability — ``GET {KERNEL_API_PREFIX}/v1/runtimes/availability``
(default ``/api/v1/runtimes/availability`` — see ``app.routes.KERNEL_API_PREFIX``).

The kernel owns the runtime binaries, so it answers "can this runtime launch
here". The host reads it via ``KernelClient`` and merges it with its static
runtime registry metadata for ``GET /v1/runtimes``. See
``docs/design/runtime-model-compat-single-source.md`` §3.3.
"""

from __future__ import annotations

from typing import Any

from app.routes import KERNEL_API_PREFIX
from fastapi import APIRouter
from src.runtimes.availability import probe_runtime_availability

router = APIRouter(prefix=f"{KERNEL_API_PREFIX}/v1/runtimes", tags=["runtimes"])


@router.get("/availability")
async def get_runtime_availability() -> dict[str, Any]:
    """Live per-runtime availability, probed in this kernel's environment."""
    return {"data": probe_runtime_availability()}


@router.get("/bg-busy-sessions")
async def get_bg_busy_sessions() -> dict[str, Any]:
    """Session ids of warm runtimes with live background tasks.

    Process-scoped, id-only (the orchestrator holds no owner index); callers
    intersect with their own owner-scoped session set. Kernel-internal auth
    covers the surface — the host is the only caller."""
    from app.dependencies import get_orchestrator

    return {"data": get_orchestrator().bg_busy_session_ids()}
