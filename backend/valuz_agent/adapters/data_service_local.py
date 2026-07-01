"""In-process :class:`~valuz_agent.adapters.data_reader.DataReader` over the
host-mounted DataService store.

Reads are **unified through the DataService**: whenever a durable DataService is
configured, the host reads kernel sessions + event history straight from its
backend — in-process, independent of the (possibly dead) sandbox kernel. There is
no "is the sandbox alive?" branch: the sandbox owns *execution* (run + live
deltas); the DataService owns *history*.

This is the default OSS implementation of the ``DataReader`` port. The composition
root (``boot/steps.py``) wraps the host store in this reader and binds it via
``bind_data_reader``; a SaaS overlay can bind a different ``DataReader`` instead.

It exposes the read surface the host consumes — session fetches
(``get_session`` / ``list_sessions``, projected to ``SessionData`` via the kernel
route's ``session_to_data``) and event history (``get_events(after_seq=…)`` /
``get_events_window``, adapting the StorePort's ``get_events_after`` /
``get_events_window`` whose ``StoredEvent``s already carry
``seq``/``data``/``message_id``/``type``/``timestamp``).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from src.core.store_port import StorePort


class LocalDataServiceReader:
    """StorePort → the SSE adapter's history-read surface, in-process."""

    def __init__(self, store: StorePort) -> None:
        self._store = store

    async def get_events(
        self,
        user_id: str,
        session_id: str,
        *,
        limit: int = 200,
        offset: int = 0,
        after_seq: int | None = None,
    ) -> list[Any]:
        return await self._store.get_events_after(
            user_id, session_id, after_seq=after_seq or 0, limit=limit
        )

    async def get_events_window(
        self,
        user_id: str,
        session_id: str,
        *,
        before_seq: int | None = None,
        turn_limit: int = 20,
    ) -> Any:
        items, has_more = await self._store.get_events_window(
            user_id, session_id, before_seq=before_seq, turn_limit=turn_limit
        )
        # ``event_sse_adapter`` reads ``.items`` + ``.has_more``; StoredEvents
        # already carry the attributes the frame translator needs.
        return SimpleNamespace(items=items, has_more=has_more)

    # -- Session reads (DataService design §5: session fetches go through the
    # DataService, never the execution-local sqlite). Projected to ``SessionData``
    # via the same serializer the kernel route uses, so the host-side session
    # mappers consume this interchangeably with the ``KernelClient`` seam.
    async def get_session(self, user_id: str, session_id: str) -> Any:
        from app.serializers import session_to_data

        session = await self._store.load_session(user_id, session_id)
        return session_to_data(session) if session is not None else None

    async def list_sessions(
        self,
        user_id: str,
        *,
        status: str | None = None,
        ids: list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Any]:
        from app.serializers import session_to_data

        sessions = await self._store.list_sessions(
            user_id, status=status, ids=ids, limit=limit, offset=offset
        )
        return [session_to_data(s) for s in sessions]
