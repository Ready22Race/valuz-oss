"""HTTP layer for ``GET /v1/system/status``.

The desktop ``服务`` page (status card + log viewer) hits this once on
mount and again every few seconds. Cheap on every call — the heavy
lifting (kernel pin parse, version read) is memoised inside
``service.collect_system_status``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from valuz_agent.modules.system.schemas import SystemStatusResponse
from valuz_agent.modules.system.service import (
    collect_system_status,
    listen_port,
)

router = APIRouter(prefix="/v1/system", tags=["system"])


class NetworkEgressReconfigureRequest(BaseModel):
    bootstrap: dict[str, Any] | None = None
    required_unavailable: bool = False
    prewarm_limit: int = Field(default=1, ge=0, le=1)


class NetworkEgressReconfigureResponse(BaseModel):
    configured: bool
    prewarmed_session_ids: list[str]
    prewarm_failed_session_ids: list[str]


@router.get("/status", response_model=SystemStatusResponse)
async def get_system_status() -> SystemStatusResponse:
    """Snapshot of the running backend process.

    Drives the desktop ``服务`` panel. See
    ``components.schemas.SystemStatusResponse`` in
    ``api/openapi.yaml`` for the wire shape.
    """
    return await collect_system_status(port=listen_port())


@router.post("/network-egress", response_model=NetworkEgressReconfigureResponse)
async def reconfigure_network_egress(
    body: NetworkEgressReconfigureRequest,
    x_valuz_desktop_token: str | None = Header(default=None),
) -> NetworkEgressReconfigureResponse:
    """Replace desktop model networking without restarting the API process."""
    from app.dependencies import get_orchestrator
    from src.runtimes.network_egress import (
        desktop_control_authorized,
        replace_network_egress,
    )

    if not desktop_control_authorized(x_valuz_desktop_token):
        raise HTTPException(status_code=401, detail="desktop_control_unauthorized")

    orchestrator = get_orchestrator()
    if orchestrator.active_sessions:
        raise HTTPException(status_code=409, detail="model_runtimes_still_active")

    candidates = orchestrator.warm_runtime_candidates(limit=body.prewarm_limit)
    await orchestrator.evict_all_warm_runtimes()
    await replace_network_egress(
        body.bootstrap,
        required_unavailable=body.required_unavailable,
    )

    prewarmed: list[str] = []
    failed: list[str] = []
    for owner_id, session_id in candidates:
        try:
            await orchestrator.prepare_runtime(owner_id, session_id)
            prewarmed.append(session_id)
        except Exception:  # noqa: BLE001 - networking is already reconfigured
            failed.append(session_id)
    return NetworkEgressReconfigureResponse(
        configured=True,
        prewarmed_session_ids=prewarmed,
        prewarm_failed_session_ids=failed,
    )
