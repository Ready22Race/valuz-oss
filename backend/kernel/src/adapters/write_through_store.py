"""WriteThroughStore — local-first store + write-through to a durable backend.

Model A: the kernel ALWAYS writes a local copy (local-first reads + availability)
and mirrors writes to a durable backend. Two **per-tier** policies (the durability
vs. availability trade-off the deployment dictates):

**Event seq is LOCAL-authoritative.** Each store owns its own ``events`` PK
sequence (autoincrement); the two are independent and need NOT match — a reader
reads ONE store consistently, and ``event_uid`` bridges identity across stores
for idempotency. ``append_event`` therefore writes the LOCAL copy first (its
autoincrement assigns the seq), returns THAT seq (so the orchestrator's
persist→broadcast and the local-first read cursor agree), and mirrors to the
durable with its OWN autoincrement — never forcing the durable's seq onto the
local PK (doing so collides with the local store's pre-existing ids and silently
drops events).

**Strict** (``durable_required=True`` — ``remote``/sandbox): the durable mirror
must land before the call returns; a failure is **fail-loud** (propagates), so a
sandbox never dies with un-persisted data.

**Best-effort** (``durable_required=False`` — ``pg``, the OSS user's own
Postgres): the durable mirror is attempted after the local write; on ANY failure
the op is enqueued in the :class:`DurableOutbox` and the call returns normally —
a durable outage never blocks local-first writes. A background drainer re-pushes
the backlog on recovery (idempotent replay via ``event_uid`` / UUID PKs).

Common to both: a shared ``request_id`` makes the durable+local pair idempotent;
**reads are local-first**.

Constructed ONLY when ``durable`` is genuinely distinct from ``local`` — when they
resolve to the same backend the dependency layer returns the local store directly,
so there is no pointless double write.

Structurally satisfies ``src.core.StorePort`` (does not inherit the Protocol).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Sequence

from src.adapters import store_wire as sw
from src.adapters.durable_outbox import DurableOutbox
from src.core.events import Event
from src.core.store_port import StoredEvent, StorePort, UsageRollupRow
from src.core.types import Message, Session

logger = logging.getLogger(__name__)


class WriteThroughStore:
    def __init__(
        self,
        local: StorePort,
        durable: StorePort,
        *,
        durable_required: bool = True,
        outbox: DurableOutbox | None = None,
        drain_interval_s: float = 30.0,
    ) -> None:
        if not durable_required and outbox is None:
            raise ValueError("best-effort write-through requires a DurableOutbox")
        self._local = local
        self._durable = durable
        self._strict = durable_required
        self._outbox = outbox
        self._drain_interval_s = drain_interval_s
        self._drainer: asyncio.Task[None] | None = None

    # ---- lifecycle (best-effort outbox drainer) --------------------------

    def start(self) -> None:
        """Start the background outbox drainer. A no-op in strict mode (no
        outbox) and idempotent — mirrors ``SessionOrchestrator.start``. The
        first iteration re-pushes any backlog left by a prior run."""
        if self._outbox is not None and self._drainer is None:
            self._drainer = asyncio.create_task(self._drain_loop())

    async def aclose(self) -> None:
        """Stop the drainer. The backlog stays durable in the local DB and is
        re-pushed on the next ``start``. Mirrors ``SessionOrchestrator.shutdown``."""
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
        await self._local.save_session(session)
        if self._strict:
            await self._durable.save_session(session)
            return
        await self._mirror(
            self._durable.save_session(session),
            op="save_session",
            user_id=session.user_id,
            body={"session": sw.session_to_row(session)},
        )

    async def save_message(self, user_id: str, message: Message) -> None:
        await self._local.save_message(user_id, message)
        if self._strict:
            await self._durable.save_message(user_id, message)
            return
        await self._mirror(
            self._durable.save_message(user_id, message),
            op="save_message",
            user_id=user_id,
            body={"message": sw.message_to_row(message)},
        )

    async def delete_session(self, user_id: str, session_id: str) -> bool:
        deleted = await self._local.delete_session(user_id, session_id)
        if self._strict:
            await self._durable.delete_session(user_id, session_id)
            return deleted
        await self._mirror(
            self._durable.delete_session(user_id, session_id),
            op="delete_session",
            user_id=user_id,
            body={"session_id": session_id},
        )
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
        # LOCAL is the seq authority: its autoincrement assigns the seq we return
        # (reads + broadcast use it). The durable mirror autoincrements its OWN
        # seq independently — NEVER force the durable's seq onto the local PK
        # (that collides with the local store's existing ids and drops events).
        local_seq = await self._local.append_event(
            user_id, session_id, message_id, event, request_id=rid
        )
        if self._strict:
            # Durable mirror must land before returning; failure is fail-loud.
            await self._durable.append_event(
                user_id, session_id, message_id, event, request_id=rid
            )
            return local_seq
        # Best-effort: mirror after the local write; on failure queue for replay.
        await self._mirror(
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

    async def _mirror(self, coro, *, op: str, user_id: str, body: dict) -> None:
        """Best-effort durable mirror: await ``coro``; on ANY failure enqueue the
        op for later re-push so the (already-committed) local write is never
        blocked by a durable outage."""
        assert self._outbox is not None  # guaranteed by __init__ in best-effort mode
        try:
            await coro
        except Exception as exc:  # noqa: BLE001 — availability over consistency here
            logger.warning("durable mirror failed (op=%s); queued to outbox: %s", op, exc)
            await self._outbox.enqueue(op, user_id, body)

    async def drain_outbox(self) -> int:
        """Re-push any queued durable writes (best-effort mode). No-op in strict
        mode. Returns the number of ops drained."""
        if self._outbox is None:
            return 0
        return await self._outbox.drain(self._durable)

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
