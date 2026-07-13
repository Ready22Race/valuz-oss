"""HTTP routes for the unified notification ledger (docs/design/notifications.md).

Supersedes ``/v1/decisions/*`` (questions are now a notification kind) and the
interim ``/v1/tasks/attention`` poll (failures stream here). One snapshot + one
SSE + read/dismiss — the frontend faces a single, backend-reconciled account.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Request
from sse_starlette.sse import EventSourceResponse

from valuz_agent.api.deps import get_current_user_id
from valuz_agent.modules.notifications.schemas import NotificationListResponse
from valuz_agent.modules.notifications.service import notification_service

router = APIRouter()


@router.get("/v1/notifications", response_model=NotificationListResponse)
async def list_notifications(
    user_id: str = Depends(get_current_user_id),
) -> NotificationListResponse:
    """Open (unresolved) notifications + the unread count, for cold-start."""
    entries, unread = await notification_service.snapshot(user_id)
    return NotificationListResponse(entries=entries, unread=unread)


@router.get("/v1/notifications/stream")
async def stream_notifications(
    request: Request,
    user_id: str = Depends(get_current_user_id),
) -> EventSourceResponse:
    """SSE: first frame is a ``snapshot``; then ``added`` / ``updated`` (read)
    / ``resolved`` deltas as the service fans them out."""

    async def event_source() -> AsyncIterator[dict[str, str]]:
        queue = await notification_service.subscribe(user_id)
        try:
            while True:
                if await request.is_disconnected():
                    break
                ev = await queue.get()
                if ev is None:  # shutdown sentinel
                    break
                yield {"event": ev.kind, "data": ev.model_dump_json()}
        finally:
            await notification_service.unsubscribe(queue)

    return EventSourceResponse(event_source(), ping=30)


@router.post("/v1/notifications/{notification_id}:read")
async def mark_read(
    notification_id: str,
    user_id: str = Depends(get_current_user_id),
) -> dict[str, bool]:
    await notification_service.mark_read(user_id, notification_id)
    return {"ok": True}


@router.post("/v1/notifications:read-all")
async def mark_all_read(
    user_id: str = Depends(get_current_user_id),
) -> dict[str, bool]:
    await notification_service.mark_all_read(user_id)
    return {"ok": True}


@router.post("/v1/notifications/{notification_id}:dismiss")
async def dismiss(
    notification_id: str,
    user_id: str = Depends(get_current_user_id),
) -> dict[str, bool]:
    await notification_service.dismiss(user_id, notification_id)
    return {"ok": True}


__all__ = ["router"]
