"""Cross-session event routes — /api/v1/events.

The global stream is the remote analog of the in-process
``SessionOrchestrator.attach_global_tap``: one SSE connection carries
``(session_id, event)`` for every session, so host-level aggregators
(decision inbox, activity overview) follow the whole kernel without
opening N per-session streams.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Annotated, Any

from app.dependencies import get_orchestrator
from app.event_stream import GlobalQueueTap
from app.serializers import live_event_to_data
from fastapi import APIRouter, Depends, Request
from sse_starlette.sse import EventSourceResponse

router = APIRouter(prefix="/api/v1/events", tags=["events"])

STREAM_HEARTBEAT_SECONDS = 15.0


@router.get("/stream")
async def stream_all_events(
    request: Request,
    orchestrator: Annotated[Any, Depends(get_orchestrator)],
) -> EventSourceResponse:
    """Live event stream across ALL sessions, as Server-Sent Events.

    Live-only by design: aggregators hydrate their initial state from the
    REST surface (sessions + events queries), then follow this stream.
    Each frame's payload carries ``session_id``.
    """

    async def _frames() -> AsyncIterator[dict[str, Any]]:
        tap = GlobalQueueTap()
        orchestrator.attach_global_tap(tap)
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    session_id, event = await asyncio.wait_for(
                        tap.queue.get(), timeout=STREAM_HEARTBEAT_SECONDS
                    )
                except TimeoutError:
                    yield {"event": "heartbeat", "data": "{}"}
                    continue
                yield {
                    "event": "event",
                    "data": live_event_to_data(
                        event, session_id=session_id
                    ).model_dump_json(),
                }
        finally:
            orchestrator.detach_global_tap(tap)

    return EventSourceResponse(_frames())
