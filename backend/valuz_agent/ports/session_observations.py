"""Port: host-side observations injected onto a session's event stream.

Some things a client waits on are not produced by a runtime and have no
persisted representation — most of all, **bringing the session's sandbox up**.
``run_turn`` blocks on the allocator while an instance is provisioned,
bootstrapped over envd and its kernel service comes up; during that window
there is no kernel to speak for the session, so the client's SSE stream carries
nothing but heartbeats.

This port is the channel for exactly that class of frame: a HOST observation
about a session, injected straight into ``iter_events_sse`` without going
through a kernel.

Why not the kernel:

- ``emit_live_event`` needs a live kernel, which is precisely what does not
  exist yet during a cold boot; and once one does, it is a brand-new instance
  the host's live tap has not rebound onto, so the frame lands in an empty room.
- ``append_event`` persists, which is wrong for an observation — and it 404s
  anyway while the session row has not reached the new kernel's store yet
  (verified live: a 2291ms provision followed by
  ``POST /kernel/v1/sessions/{id}/events -> 404``).

Frames published here are **live-only**: no seq, no ``event_uid``, never
persisted, never replayed. A client that is not connected simply misses them,
which is the right semantics for progress — it is stale the moment it is over.

OSS boots with :class:`InProcessSessionObservations`, which fans out within one
process. That is complete for a single-process host (desktop) and for a
single-replica deployment. A multi-replica overlay rebinds this port with a
transport of its own (the publisher and the SSE subscriber can land on
different replicas), keeping the same interface.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

logger = logging.getLogger(__name__)

# Bounded per-subscriber queue. Progress frames are low-rate (a handful per
# sandbox boot); a subscriber that cannot keep up with even that is not one
# worth blocking a provision for, so the publisher drops instead of waiting.
_QUEUE_MAX = 64


class SessionObservationsPort(ABC):
    """Publish/subscribe for host observations about one session."""

    @abstractmethod
    async def publish(self, user_id: str, session_id: str, type: str, data: dict[str, Any]) -> None:
        """Fan a frame out to whoever is streaming this session right now.

        MUST NOT raise and MUST NOT block on slow subscribers: callers publish
        from inside provisioning, where the work being narrated matters far more
        than the narration.
        """
        ...

    @abstractmethod
    def subscribe(self, user_id: str, session_id: str) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """Yield ``(type, data)`` frames for this session until cancelled."""
        ...


class InProcessSessionObservations(SessionObservationsPort):
    """Default: fan out to subscribers in this process."""

    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue[tuple[str, dict[str, Any]]]]] = {}

    @staticmethod
    def _key(user_id: str, session_id: str) -> str:
        # Owner-qualified: session ids are globally unique, but keying on the
        # pair keeps a mis-scoped publish from reaching another owner's stream.
        return f"{user_id}:{session_id}"

    async def publish(self, user_id: str, session_id: str, type: str, data: dict[str, Any]) -> None:
        for queue in tuple(self._subs.get(self._key(user_id, session_id), ())):
            try:
                queue.put_nowait((type, dict(data)))
            except asyncio.QueueFull:
                logger.debug("session observation dropped (slow subscriber): %s", type)

    async def subscribe(
        self, user_id: str, session_id: str
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        key = self._key(user_id, session_id)
        queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue(maxsize=_QUEUE_MAX)
        self._subs.setdefault(key, set()).add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            subs = self._subs.get(key)
            if subs is not None:
                subs.discard(queue)
                if not subs:
                    self._subs.pop(key, None)


async def publish_session_observation(
    user_id: str, session_id: str, type: str, data: dict[str, Any]
) -> None:
    """Best-effort publish through the bound port — never raises.

    The one entry point producers should use: a failure here must never
    disturb the operation being narrated.
    """
    from valuz_agent.ports.extensions import ext

    with contextlib.suppress(Exception):
        await ext.session_observations.publish(user_id, session_id, type, data)


def set_session_observations(port: SessionObservationsPort) -> None:
    """Replace the port (called by an overlay at startup)."""
    from valuz_agent.ports.extensions import ext

    ext.session_observations = port


__all__ = [
    "InProcessSessionObservations",
    "SessionObservationsPort",
    "publish_session_observation",
    "set_session_observations",
]
