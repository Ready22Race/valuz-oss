"""WriteThroughStore — local-first store + synchronous write-through to durable.

Model A: the kernel ALWAYS writes a local copy (local-first reads + availability)
and, when a durable backend is configured, **synchronously mirrors writes** to it.

- **events** are the SaaS-centralization authority for ordering: ``append_event`` is
  **durable-first** — the durable store assigns the authoritative ``seq``
  (autoincrement) and the local copy is then written with that explicit ``seq``, so
  both copies (and a future central store) share one ordering. A shared
  ``request_id`` makes the pair idempotent under retry (``event_uid``).
- **sessions / messages / delete** are local-then-durable (client-UUID PKs, no seq
  concern; each store upserts).
- **reads** are local-first.

Constructed ONLY when ``durable`` is genuinely distinct from ``local`` — when they
resolve to the same backend the dependency layer returns the local store directly,
so there is no pointless double write.

Structurally satisfies ``src.core.StorePort`` (does not inherit the Protocol).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from src.core.events import Event
from src.core.store_port import StoredEvent, StorePort, UsageRollupRow
from src.core.types import Message, Session


class WriteThroughStore:
    def __init__(self, local: StorePort, durable: StorePort) -> None:
        self._local = local
        self._durable = durable

    # ---- writes ----------------------------------------------------------

    async def save_session(self, session: Session) -> None:
        await self._local.save_session(session)
        await self._durable.save_session(session)

    async def save_message(self, user_id: str, message: Message) -> None:
        await self._local.save_message(user_id, message)
        await self._durable.save_message(user_id, message)

    async def delete_session(self, user_id: str, session_id: str) -> bool:
        deleted = await self._local.delete_session(user_id, session_id)
        await self._durable.delete_session(user_id, session_id)
        return deleted

    async def append_event(
        self,
        user_id: str,
        session_id: str,
        message_id: str,
        event: Event,
        *,
        request_id: str | None = None,
        seq: int | None = None,
    ) -> int | None:
        rid = request_id or uuid.uuid4().hex  # one idempotency key for both copies
        # Durable assigns the authoritative seq (central ordering for SaaS)…
        durable_seq = await self._durable.append_event(
            user_id, session_id, message_id, event, request_id=rid, seq=seq
        )
        # …then mirror locally with that exact seq (idempotent on event_uid).
        await self._local.append_event(
            user_id, session_id, message_id, event, request_id=rid, seq=durable_seq
        )
        return durable_seq

    # ---- reads (local-first) --------------------------------------------

    async def load_session(self, user_id: str, session_id: str) -> Session | None:
        return await self._local.load_session(user_id, session_id)

    async def list_sessions(
        self,
        user_id: str | None,
        *,
        status: str | None = None,
        ids: Sequence[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Session]:
        return await self._local.list_sessions(
            user_id, status=status, ids=ids, limit=limit, offset=offset
        )

    async def load_message(self, user_id: str, message_id: str) -> Message | None:
        return await self._local.load_message(user_id, message_id)

    async def list_messages_for_session(
        self, user_id: str, session_id: str, *, limit: int = 50, offset: int = 0
    ) -> list[Message]:
        return await self._local.list_messages_for_session(
            user_id, session_id, limit=limit, offset=offset
        )

    async def get_events(
        self, user_id: str, session_id: str, *, limit: int = 200, offset: int = 0
    ) -> list[Event]:
        return await self._local.get_events(user_id, session_id, limit=limit, offset=offset)

    async def get_events_for_message(
        self, user_id: str, message_id: str, *, limit: int = 200, offset: int = 0
    ) -> list[Event]:
        return await self._local.get_events_for_message(
            user_id, message_id, limit=limit, offset=offset
        )

    async def get_events_after(
        self, user_id: str, session_id: str, *, after_seq: int = 0, limit: int = 200
    ) -> list[StoredEvent]:
        return await self._local.get_events_after(
            user_id, session_id, after_seq=after_seq, limit=limit
        )

    async def get_events_window(
        self, user_id: str, session_id: str, *, before_seq: int | None = None, turn_limit: int = 20
    ) -> tuple[list[StoredEvent], bool]:
        return await self._local.get_events_window(
            user_id, session_id, before_seq=before_seq, turn_limit=turn_limit
        )

    async def usage_rollup(
        self, user_id: str, start_ms: int, end_ms: int
    ) -> list[UsageRollupRow]:
        return await self._local.usage_rollup(user_id, start_ms, end_ms)
