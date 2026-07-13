"""Runtime availability — ``GET /api/v1/runtimes/availability``.

The kernel owns the runtime binaries, so it answers "can this runtime launch
here". The host reads it via ``KernelClient`` and merges it with its static
runtime registry metadata for ``GET /v1/runtimes``. See
``docs/design/runtime-model-compat-single-source.md`` §3.3.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from src.runtimes.availability import probe_runtime_availability

router = APIRouter(prefix="/api/v1/runtimes", tags=["runtimes"])


@router.get("/availability")
async def get_runtime_availability() -> dict[str, Any]:
    """Live per-runtime availability, probed in this kernel's environment."""
    return {"data": probe_runtime_availability()}
