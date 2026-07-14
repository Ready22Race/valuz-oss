"""NotificationService — the SINGLE writer + fan-out for the notification
ledger (docs/design/notifications.md §0.2).

Every source (question projector, failure projector, …) calls ``ingest`` /
``resolve``; every delivery surface reads one snapshot + one SSE stream. The
service owns the durable table (via the datastore) AND a per-owner in-memory
subscriber fan-out for SSE — so consistency is resolved here, never in the
frontend.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.notifications.datastore import NotificationDatastore
from valuz_agent.modules.notifications.models import NotificationRow
from valuz_agent.modules.notifications.schemas import (
    NotificationEntry,
    NotificationStreamEvent,
)

logger = logging.getLogger(__name__)


def _entry(row: NotificationRow) -> NotificationEntry:
    return NotificationEntry.model_validate(row)


class NotificationService:
    def __init__(self) -> None:
        # (owner_user_id, queue) — fan-out filtered by owner (multi-tenant safe).
        self._subscribers: list[tuple[str, asyncio.Queue[NotificationStreamEvent | None]]] = []
        self._lock = asyncio.Lock()

    # ---- Sources (projectors call these) ----------------------------

    async def ingest(
        self,
        user_id: str,
        *,
        dedup_key: str,
        kind: str,
        title: str,
        body: str = "",
        route: str | None = None,
        action: str = "none",
        urgency: str = "actionable",
        task_id: str | None = None,
        project_id: str | None = None,
        session_id: str | None = None,
        pending_id: str | None = None,
        source_event_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> NotificationEntry | None:
        """Idempotent create. Broadcasts ``added`` only on a genuine insert (a
        re-fire is a silent no-op). Best-effort: a store failure is logged, not
        raised, so a projector never breaks its own event flow."""
        try:
            async with async_unit_of_work() as db:
                row, created = await NotificationDatastore(db).upsert(
                    user_id,
                    dedup_key=dedup_key,
                    kind=kind,
                    title=title,
                    body=body,
                    route=route,
                    action=action,
                    urgency=urgency,
                    task_id=task_id,
                    project_id=project_id,
                    session_id=session_id,
                    pending_id=pending_id,
                    source_event_id=source_event_id,
                    payload=payload,
                )
                entry = _entry(row)
        except Exception:  # noqa: BLE001
            logger.warning("notifications: ingest failed for %s", dedup_key, exc_info=True)
            return None
        if created:
            await self._broadcast(user_id, NotificationStreamEvent(
                kind="added", payload={"entry": entry.model_dump(mode="json")}
            ))
        return entry

    async def resolve(self, user_id: str, dedup_key: str) -> None:
        """Mark the notification behind ``dedup_key`` resolved + broadcast."""
        try:
            async with async_unit_of_work() as db:
                row = await NotificationDatastore(db).resolve_by_dedup(user_id, dedup_key)
                rid = row.id if row is not None else None
        except Exception:  # noqa: BLE001
            logger.warning("notifications: resolve failed for %s", dedup_key, exc_info=True)
            return
        if rid is not None:
            await self._broadcast(
                user_id, NotificationStreamEvent(kind="resolved", payload={"id": rid})
            )

    async def resolve_pending(self, pending_id: str) -> None:
        """Resolve a question notification by its (globally-unique) ``pending_id``
        without the owner up front — the decisions aggregator calls this on
        ``action_resolved`` for conversation questions, which it never tracks in
        memory (so no owner is known). The datastore row carries ``user_id`` for
        the fan-out. Idempotent + best-effort (a store failure is logged)."""
        try:
            async with async_unit_of_work() as db:
                row = await NotificationDatastore(db).resolve_by_pending_id(pending_id)
                target = (row.id, row.user_id) if row is not None else None
        except Exception:  # noqa: BLE001
            logger.warning(
                "notifications: resolve_pending failed for %s", pending_id, exc_info=True
            )
            return
        if target is not None:
            rid, uid = target
            await self._broadcast(
                uid, NotificationStreamEvent(kind="resolved", payload={"id": rid})
            )

    async def resolve_task(
        self, user_id: str, task_id: str, kinds: tuple[str, ...] = ("task_failed",)
    ) -> None:
        """Resolve every open ``kinds`` notification for a task — called when the
        task is resumed / abandoned so a stale "failed" item doesn't keep the
        badge lit after the user has dealt with it. Best-effort."""
        try:
            async with async_unit_of_work() as db:
                ids = await NotificationDatastore(db).resolve_open_by_task(user_id, task_id, kinds)
        except Exception:  # noqa: BLE001
            logger.warning("notifications: resolve_task failed for %s", task_id, exc_info=True)
            return
        for rid in ids:
            await self._broadcast(
                user_id, NotificationStreamEvent(kind="resolved", payload={"id": rid})
            )

    async def resolve_session_failures(
        self, user_id: str, session_id: str, kinds: tuple[str, ...] = ("run_failed",)
    ) -> None:
        """Resolve every open ``kinds`` notification for a conversation session —
        called on a clean turn so a recovered conversation doesn't keep the badge
        lit with a stale failure. Best-effort."""
        try:
            async with async_unit_of_work() as db:
                ids = await NotificationDatastore(db).resolve_open_by_session(
                    user_id, session_id, kinds
                )
        except Exception:  # noqa: BLE001
            logger.warning(
                "notifications: resolve_session_failures failed for %s", session_id, exc_info=True
            )
            return
        for rid in ids:
            await self._broadcast(
                user_id, NotificationStreamEvent(kind="resolved", payload={"id": rid})
            )

    # ---- Reads / user actions (routes call these) -------------------

    async def snapshot(self, user_id: str) -> tuple[list[NotificationEntry], int]:
        async with async_unit_of_work(commit=False) as db:
            ds = NotificationDatastore(db)
            rows = await ds.list_open(user_id)
            unread = await ds.count_unread(user_id)
        return [_entry(r) for r in rows], unread

    async def mark_read(self, user_id: str, notification_id: str) -> None:
        async with async_unit_of_work() as db:
            row = await NotificationDatastore(db).mark_read(user_id, notification_id)
            entry = _entry(row) if row is not None else None
        if entry is not None:
            await self._broadcast(
                user_id,
                NotificationStreamEvent(
                    kind="updated", payload={"entry": entry.model_dump(mode="json")}
                ),
            )

    async def mark_all_read(self, user_id: str) -> None:
        async with async_unit_of_work() as db:
            changed = await NotificationDatastore(db).mark_all_read(user_id)
        if changed:
            # Cheapest correct signal: push a fresh snapshot so every client
            # reconciles unread=0 without N per-row frames.
            entries, unread = await self.snapshot(user_id)
            await self._broadcast(
                user_id,
                NotificationStreamEvent(
                    kind="snapshot",
                    payload={
                        "entries": [e.model_dump(mode="json") for e in entries],
                        "unread": unread,
                    },
                ),
            )

    async def dismiss(self, user_id: str, notification_id: str) -> None:
        """User-driven resolve (e.g. swiping away a failure they acknowledge)."""
        async with async_unit_of_work() as db:
            row = await NotificationDatastore(db).resolve_by_id(user_id, notification_id)
            rid = row.id if row is not None else None
        if rid is not None:
            await self._broadcast(
                user_id, NotificationStreamEvent(kind="resolved", payload={"id": rid})
            )

    # ---- SSE fan-out ------------------------------------------------

    async def subscribe(self, user_id: str) -> asyncio.Queue[NotificationStreamEvent | None]:
        queue: asyncio.Queue[NotificationStreamEvent | None] = asyncio.Queue(maxsize=256)
        entries, unread = await self.snapshot(user_id)
        # First frame is always a full snapshot so a reconnecting client
        # converges without gaps.
        await queue.put(
            NotificationStreamEvent(
                kind="snapshot",
                payload={
                    "entries": [e.model_dump(mode="json") for e in entries],
                    "unread": unread,
                },
            )
        )
        async with self._lock:
            self._subscribers.append((user_id, queue))
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[NotificationStreamEvent | None]) -> None:
        async with self._lock:
            self._subscribers = [(o, q) for (o, q) in self._subscribers if q is not queue]

    async def _broadcast(self, user_id: str, ev: NotificationStreamEvent) -> None:
        async with self._lock:
            for owner, q in self._subscribers:
                if owner != user_id:
                    continue
                try:
                    q.put_nowait(ev)
                except asyncio.QueueFull:
                    logger.warning("notifications: subscriber queue full, dropping %s", ev.kind)

    async def stop(self) -> None:
        async with self._lock:
            for _owner, q in self._subscribers:
                try:
                    q.put_nowait(None)
                except asyncio.QueueFull:
                    pass
            self._subscribers.clear()


# Process singleton.
notification_service = NotificationService()
