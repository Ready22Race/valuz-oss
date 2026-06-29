"""DurableOutbox — transactional outbox for best-effort write-through.

Backs ``kernel_store=pg`` (best-effort tier). When a durable mirror write fails
(e.g. the OSS user's Postgres is briefly down), :class:`WriteThroughStore`
records the op here so the LOCAL write still succeeds (local-first availability)
and a background drainer re-pushes it once the durable store recovers. The body
is the :mod:`store_wire` payload for the op, so replay is idempotent (UUID PKs /
``event_uid``) — at-least-once redelivery is safe.

Rows are drained in insertion order (``id`` ascending). A replay failure stops
the drain (transient outages fail every row equally; preserving order matters
for a session's events) — the row's ``attempts`` / ``last_error`` are bumped so
the backlog is observable. Unused in strict (``remote``/sandbox) mode, where a
durable failure is fail-loud.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from src.adapters import store_wire as sw
from src.adapters.sqlalchemy_store.models import DurableOutboxModel
from src.core.store_port import StorePort

logger = logging.getLogger(__name__)


class DurableOutbox:
    """Local-DB-backed outbox of failed durable write-through ops."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def enqueue(self, op: str, user_id: str, body: dict[str, Any]) -> None:
        async with self._sf() as session:
            session.add(DurableOutboxModel(op=op, user_id=user_id, body=body))
            await session.commit()

    async def pending_count(self) -> int:
        async with self._sf() as session:
            rows = (await session.execute(select(DurableOutboxModel.id))).all()
            return len(rows)

    async def drain(self, durable: StorePort, *, limit: int = 500) -> int:
        """Replay queued ops to ``durable`` in order. Returns the count drained.

        Stops at the first replay failure (transient outage → every row fails;
        ordering is preserved). The failed row's ``attempts``/``last_error`` are
        recorded before returning.
        """
        async with self._sf() as session:
            rows = (
                (
                    await session.execute(
                        select(DurableOutboxModel)
                        .order_by(DurableOutboxModel.id.asc())
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            drained = 0
            for row in rows:
                try:
                    await self._replay(durable, row)
                except Exception as exc:  # noqa: BLE001 — durable still unhealthy
                    await session.execute(
                        update(DurableOutboxModel)
                        .where(DurableOutboxModel.id == row.id)
                        .values(attempts=row.attempts + 1, last_error=str(exc)[:500])
                    )
                    await session.commit()
                    logger.warning(
                        "durable outbox drain stopped at row %s (op=%s): %s",
                        row.id,
                        row.op,
                        exc,
                    )
                    return drained
                await session.execute(
                    delete(DurableOutboxModel).where(DurableOutboxModel.id == row.id)
                )
                await session.commit()
                drained += 1
            return drained

    @staticmethod
    async def _replay(durable: StorePort, row: DurableOutboxModel) -> None:
        op, uid, body = row.op, row.user_id, row.body
        if op == "save_session":
            await durable.save_session(sw.row_to_session(body["session"]))
        elif op == "save_message":
            await durable.save_message(uid, sw.row_to_message(body["message"]))
        elif op == "delete_session":
            await durable.delete_session(uid, body["session_id"])
        elif op == "append_event":
            await durable.append_event(
                uid,
                body["session_id"],
                body["message_id"],
                sw.row_to_event(body["event"]),
                request_id=body.get("request_id"),
                seq=body.get("seq"),
            )
        else:  # pragma: no cover — guarded by enqueue call sites
            raise ValueError(f"unknown outbox op {op!r}")
