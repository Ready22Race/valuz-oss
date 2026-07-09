"""HTTP surface for the Runtime Agent registry.

The frontend's session-creation flow calls ``GET /v1/runtimes`` to render
the Runtime picker. The response carries everything the picker needs:
the display label, which API protocols the runtime can dispatch (used
to filter compatible channels), and a live ``available`` probe so the
UI can grey out runtimes that aren't actually runnable (typically Codex
when the ``codex`` binary is missing).

Static metadata (display label + supported protocols) comes from
``valuz_agent.adapters.runtime_registry.RUNTIME_REGISTRY``; the live
``available`` flag is reported by the **kernel** through ``KernelClient``
(``runtime_availability``), so it reflects wherever the kernel runs — the
local process (bundled desktop) or a cloud sandbox — not the API host's PATH.
See ``docs/design/runtime-model-compat-single-source.md`` §3.3.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel

from valuz_agent.adapters import kernel_client
from valuz_agent.adapters.runtime_registry import (
    is_runtime_available,
    list_runtimes,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/runtimes", tags=["runtimes"])


class RuntimeListItem(BaseModel):
    id: str
    display_name: str
    supported_protocols: list[str]
    requires_binary: str | None
    available: bool
    unavailable_reason: str | None


@router.get("")
async def list_runtime_endpoints() -> dict[str, list[RuntimeListItem]]:
    """Return every runtime + live (kernel-reported) availability for the picker."""
    try:
        availability = await kernel_client.runtime_availability()
    except Exception:  # noqa: BLE001
        # Kernel unreachable: fall back to the local host probe so the picker
        # still renders (correct for the in-process/bundled case; a remote
        # kernel outage degrades to the API host's view rather than an error).
        logger.warning(
            "kernel runtime_availability failed; falling back to local probe",
            exc_info=True,
        )
        availability = {}

    items: list[RuntimeListItem] = []
    for spec in list_runtimes():
        entry = availability.get(spec.id)
        if entry is not None:
            available = bool(entry.get("available"))
            reason = entry.get("unavailable_reason")
        else:
            available, reason = is_runtime_available(spec.id)
        items.append(
            RuntimeListItem(
                id=spec.id,
                display_name=spec.display_name,
                supported_protocols=list(spec.supported_protocols),
                requires_binary=spec.requires_binary,
                available=available,
                unavailable_reason=reason,
            )
        )
    return {"runtimes": items}
