"""WriteThroughStore — dual-write store with a per-tier authoritative side.

The kernel always writes BOTH a local copy and a durable copy; which side is the
**authority** (the read source + the seq source) depends on the deployment tier,
because the trade-offs differ:

**``authority="local"``** — the ``pg`` tier (OSS, resident process + the user's
own Postgres). The LOCAL store is authoritative: reads come from local, the event
``seq`` is the local autoincrement, and the durable (Postgres) is a **best-effort
mirror** for centralization/backup. A durable failure is queued in the
:class:`DurableOutbox` and re-pushed on recovery, so a Postgres outage never
blocks local-first writes.

**``authority="durable"``** — the ``remote`` tier (SaaS, **ephemeral** sandbox).
The DURABLE store (the central DataService) is the system of record: reads come
from durable, the event ``seq`` is the durable's central autoincrement, and the
durable write is **fail-loud** (must land before returning — the sandbox can be
destroyed at any time, so nothing may be left only in sandbox-local storage). The
LOCAL store is a **best-effort write buffer** (fast/offline copy, never the read
source); a local-buffer failure is logged, never fatal.

Common to both: each store owns its OWN ``events`` autoincrement — the two
sequences are independent and need not match (``event_uid`` bridges identity for
idempotency). NEVER force one store's seq onto the other's PK: the local
``kernel.db`` usually already holds overlapping ids, so forcing collides and
silently drops events. ``append_event`` returns the AUTHORITY's seq, so the
orchestrator's persist→broadcast and the read cursor always agree.

Constructed ONLY when ``durable`` is genuinely distinct from ``local``.
Structurally satisfies ``src.core.StorePort`` (does not inherit the Protocol).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Sequence
from typing import Literal

from src.adapters import store_wire as sw
from src.adapters.durable_outbox import DurableOutbox
from src.core.events import Event
from src.core.store_port import StoredEvent, StorePort, UsageRollupRow
from src.core.types import Message, Session

logger = logging.getLogger(__name__)

Authority = Literal["local", "durable"]


class WriteThroughStore:
    def __init__(
        self,
        local: StorePort,
        durable: StorePort,
        *,
        authority: Authority = "local",
        outbox: DurableOutbox | None = None,
        drain_interval_s: float = 30.0,
    ) -> None:
        if authority not in ("local", "durable"):
            raise ValueError(f"authority must be 'local' or 'durable', got {authority!r}")
        if authority == "local" and outbox is None:
            raise ValueError("local-authority (pg) write-through requires a DurableOutbox")
        self._local = local
        self._durable = durable
        self._authority: Authority = authority
        # The read source + seq source. Writes always hit both stores.
        self._read: StorePort = local if authority == "local" else durable
        # Outbox backs the best-effort DURABLE mirror (local-authority only). In
        # durable-authority mode the best-effort side is the LOCAL buffer, which
        # needs no replay queue (durable already holds the authoritative copy).
        self._outbox = outbox
        self._drain_interval_s = drain_interval_s
        self._drainer: asyncio.Task[None] | None = None

    # ---- lifecycle (best-effort outbox drainer; local-authority only) ----

    def start(self) -> None:
        """Start the background outbox drainer. No-op without an outbox
        (durable-authority / local-only). Idempotent; mirrors
        ``SessionOrchestrator.start``. The first iteration re-pushes any backlog
        left by a prior run."""
        if self._outbox is not None and self._drainer is None:
            self._drainer = asyncio.create_task(self._drain_loop())

    async def aclose(self) -> None:
        """Stop the drainer. The backlog stays durable in the local DB and is
        re-pushed on the next ``start``."""
        if self._drainer is not None:
            self._drainer.cancel()
            try:
                await self._drainer
            except asyncio.CancelledError:
                pass
            self._drainer = None

    async def _drain_loop(self) -> None:
        while True:
            try:
                drained = await self.drain_outbox()
                if drained:
                    logger.info("durable outbox: re-pushed %d op(s)", drained)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — keep the loop alive across blips
                logger.debug("outbox drain loop iteration failed", exc_info=True)
            await asyncio.sleep(self._drain_interval_s)

    # ---- writes ----------------------------------------------------------

    async def save_session(self, session: Session) -> None:
        if self._authority == "local":
            await self._local.save_session(session)
            await self._mirror_durable(
                self._durable.save_session(session),
                op="save_session",
                user_id=session.user_id,
                body={"session": sw.session_to_row(session)},
            )
        else:
            await self._durable.save_session(session)  # authoritative, fail-loud
            await self._buffer_local(self._local.save_session(session))

    async def save_message(self, user_id: str, message: Message) -> None:
        if self._authority == "local":
            await self._local.save_message(user_id, message)
            await self._mirror_durable(
                self._durable.save_message(user_id, message),
                op="save_message",
                user_id=user_id,
                body={"message": sw.message_to_row(message)},
            )
        else:
            await self._durable.save_message(user_id, message)
            await self._buffer_local(self._local.save_message(user_id, message))

    async def delete_session(self, user_id: str, session_id: str) -> bool:
        if self._authority == "local":
            deleted = await self._local.delete_session(user_id, session_id)
            await self._mirror_durable(
                self._durable.delete_session(user_id, session_id),
                op="delete_session",
                user_id=user_id,
                body={"session_id": session_id},
            )
            return deleted
        deleted = await self._durable.delete_session(user_id, session_id)
        await self._buffer_local(self._local.delete_session(user_id, session_id))
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
        # Each store autoincrements its OWN seq; we return the AUTHORITY's seq so
        # reads + broadcast agree. Never pass an explicit seq to either store.
        if self._authority == "local":
            local_seq = await self._local.append_event(
                user_id, session_id, message_id, event, request_id=rid
            )
            await self._mirror_durable(
                self._durable.append_event(
                    user_id, session_id, message_id, event, request_id=rid
                ),
                op="append_event",
                user_id=user_id,
                body={
                    "session_id": session_id,
                    "message_id": message_id,
                    "event": sw.event_to_row(event),
                    "request_id": rid,
                },
            )
            return local_seq
        # durable authority (remote): central seq, fail-loud; local is a buffer.
        durable_seq = await self._durable.append_event(
            user_id, session_id, message_id, event, request_id=rid
        )
        await self._buffer_local(
            self._local.append_event(user_id, session_id, message_id, event, request_id=rid)
        )
        return durable_seq

    async def _mirror_durable(self, coro, *, op: str, user_id: str, body: dict) -> None:
        """Best-effort DURABLE mirror (local-authority): on failure enqueue for
        replay so the already-committed local write is never blocked."""
        assert self._outbox is not None  # guaranteed by __init__ for local authority
        try:
            await coro
        except Exception as exc:  # noqa: BLE001 — availability over consistency
            logger.warning("durable mirror failed (op=%s); queued to outbox: %s", op, exc)
            await self._outbox.enqueue(op, user_id, body)

    async def _buffer_local(self, coro) -> None:
        """Best-effort LOCAL buffer write (durable-authority): the durable already
        holds the authoritative copy, so a local-cache failure is non-fatal."""
        try:
            await coro
        except Exception as exc:  # noqa: BLE001 — buffer is non-authoritative
            logger.warning("local buffer write failed (non-fatal): %s", exc)

    async def drain_outbox(self) -> int:
        """Re-push queued durable writes (local-authority only). 0 otherwise."""
        if self._outbox is None:
            return 0
        return await self._outbox.drain(self._durable)

    # ---- reads (from the authoritative store) ----------------------------

    async def load_session(self, user_id: str, session_id: str) -> Session | None:
        return await self._read.load_session(user_id, session_id)

    async def list_sessions(
        self,
        user_id: str | None,
        *,
        status: str | None = None,
        ids: Sequence[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Session]:
        return await self._read.list_sessions(
            user_id, status=status, ids=ids, limit=limit, offset=offset
        )

    async def load_message(self, user_id: str, message_id: str) -> Message | None:
        return await self._read.load_message(user_id, message_id)

    async def list_messages_for_session(
        self, user_id: str, session_id: str, *, limit: int = 50, offset: int = 0
    ) -> list[Message]:
        return await self._read.list_messages_for_session(
            user_id, session_id, limit=limit, offset=offset
        )

    async def get_events(
        self,
        user_id: str,
        session_id: str,
        *,
        limit: int = 200,
        offset: int = 0,
        types: Sequence[str] | None = None,
    ) -> list[Event]:
        return await self._read.get_events(
            user_id, session_id, limit=limit, offset=offset, types=types
        )

    async def get_events_for_message(
        self, user_id: str, message_id: str, *, limit: int = 200, offset: int = 0
    ) -> list[Event]:
        return await self._read.get_events_for_message(
            user_id, message_id, limit=limit, offset=offset
        )

    async def get_events_after(
        self, user_id: str, session_id: str, *, after_seq: int = 0, limit: int = 200
    ) -> list[StoredEvent]:
        return await self._read.get_events_after(
            user_id, session_id, after_seq=after_seq, limit=limit
        )

    async def get_events_window(
        self, user_id: str, session_id: str, *, before_seq: int | None = None, turn_limit: int = 20
    ) -> tuple[list[StoredEvent], bool]:
        return await self._read.get_events_window(
            user_id, session_id, before_seq=before_seq, turn_limit=turn_limit
        )

    async def usage_rollup(
        self, user_id: str, start_ms: int, end_ms: int
    ) -> list[UsageRollupRow]:
        return await self._read.usage_rollup(user_id, start_ms, end_ms)
