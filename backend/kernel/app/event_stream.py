"""Queue-backed sinks for streaming kernel events to subscribers.

Two adapters over ``asyncio.Queue``:

- :class:`QueueEventSink` — an ``EventSink`` for session-scoped taps
  (``SessionOrchestrator.attach_session_tap``).
- :class:`GlobalQueueTap` — a ``GlobalEventTap`` receiving
  ``(session_id, event)`` for every session
  (``SessionOrchestrator.attach_global_tap``).

Both drop (with a debug log) instead of blocking when the queue is full:
a slow subscriber must never stall the runtime's emit path. Consumers
that need a complete record read the DB (``get_events_after``) — the
live queue is a latency optimization, not the system of record.
"""

from __future__ import annotations

import asyncio
import logging

from src.core.events import Event

logger = logging.getLogger(__name__)

DEFAULT_QUEUE_SIZE = 4096


class QueueEventSink:
    """EventSink that enqueues events for one session's subscriber."""

    def __init__(self, maxsize: int = DEFAULT_QUEUE_SIZE) -> None:
        self.queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=maxsize)

    async def emit(self, event: Event) -> None:
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.debug("Session event queue full, dropping event %s", event.type)


class GlobalQueueTap:
    """GlobalEventTap that enqueues ``(session_id, event)`` tuples."""

    def __init__(self, maxsize: int = DEFAULT_QUEUE_SIZE * 2) -> None:
        self.queue: asyncio.Queue[tuple[str, Event]] = asyncio.Queue(maxsize=maxsize)

    async def emit_session(self, session_id: str, event: Event) -> None:
        try:
            self.queue.put_nowait((session_id, event))
        except asyncio.QueueFull:
            logger.debug(
                "Global event queue full, dropping event %s for session %s",
                event.type,
                session_id,
            )
