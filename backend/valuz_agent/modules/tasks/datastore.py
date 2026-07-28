"""Datastores for the Task, TaskEvent, and TaskSession tables.

Owner model: user-facing list/get reads take the caller's ``user_id`` first and
filter on it; writes stamp the owner explicitly. A few methods stay cross-owner
on purpose:

- ``TaskDatastore.list_active`` — startup recovery resumes every owner's active
  tasks (it threads each row's ``user_id`` downstream).
- ``TaskSessionDatastore.get_run`` / ``update_run_by_session`` /
  ``next_sequence`` — keyed on the globally-unique kernel ``session_id`` / run
  id / per-task sequence; used by the runner + kernel-event finalization, not
  user queries.

``append_event`` assigns a monotonic sequence per (project_id, task_id).
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, cast

from sqlalchemy import case, func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.infra.db import async_commit_with_retry
from valuz_agent.infra.time_utils import now_ms
from valuz_agent.modules.tasks.models import TaskEventRow, TaskRow, TaskSessionRow
from valuz_agent.modules.tasks.task_state import (
    TaskStateError,
    assert_transition,
    is_valid_status,
)

logger = logging.getLogger(__name__)

_LOCK_RETRY_ATTEMPTS = 12


async def _lock_backoff_sleep(attempt: int) -> None:
    await asyncio.sleep(min(0.05 * (2**attempt), 1.5) + random.uniform(0, 0.05))


class TaskDatastore:
    """CRUD for valuz_task rows."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # -- Queries --

    async def list_tasks(self, user_id: str, project_id: str) -> list[TaskRow]:
        return list(
            (
                await self._db.execute(
                    select(TaskRow)
                    .where(TaskRow.project_id == project_id, TaskRow.user_id == user_id)
                    .order_by(TaskRow.created_at.desc())
                )
            )
            .scalars()
            .all()
        )

    async def list_tasks_page(
        self,
        user_id: str,
        *,
        project_id: str | None = None,
        before_ts: int | None = None,
        automation: bool | None = None,
        limit: int = 20,
    ) -> list[TaskRow]:
        """Keyset page of tasks (newest ``updated_at`` first) for the unified
        activity feed. ``project_id=None`` spans every project (global 动态
        scope). ``automation`` filters by trigger: ``True`` → automation-fired
        only, ``False`` → user only, ``None`` → both. ``before_ts`` is the keyset
        cursor (strictly older ``updated_at``)."""
        stmt = select(TaskRow).where(TaskRow.user_id == user_id)
        if project_id is not None:
            stmt = stmt.where(TaskRow.project_id == project_id)
        if automation is True:
            stmt = stmt.where(TaskRow.trigger_automation_id.is_not(None))
        elif automation is False:
            stmt = stmt.where(TaskRow.trigger_automation_id.is_(None))
        if before_ts is not None:
            stmt = stmt.where(TaskRow.updated_at < before_ts)
        stmt = stmt.order_by(TaskRow.updated_at.desc()).limit(limit)
        return list((await self._db.execute(stmt)).scalars().all())

    async def get_task(self, user_id: str, task_id: str) -> TaskRow | None:
        return (
            (
                await self._db.execute(
                    select(TaskRow).where(TaskRow.id == task_id, TaskRow.user_id == user_id)
                )
            )
            .scalars()
            .first()
        )

    async def get_titles_by_ids(self, user_id: str, task_ids: list[str]) -> dict[str, str]:
        """Map task id → title for the given ids (used to label trigger provenance
        — "由 任务《title》触发" — without N+1 lookups). Missing ids are omitted."""
        if not task_ids:
            return {}
        rows = (
            await self._db.execute(
                select(TaskRow.id, TaskRow.title).where(
                    TaskRow.id.in_(task_ids), TaskRow.user_id == user_id
                )
            )
        ).all()
        return {tid: title for tid, title in rows}

    async def get_task_by_project(
        self, user_id: str, project_id: str, task_id: str
    ) -> TaskRow | None:
        return (
            (
                await self._db.execute(
                    select(TaskRow).where(
                        TaskRow.project_id == project_id,
                        TaskRow.id == task_id,
                        TaskRow.user_id == user_id,
                    )
                )
            )
            .scalars()
            .first()
        )

    async def list_all(self, user_id: str, limit: int | None = 50) -> list[TaskRow]:
        """The caller's tasks across all their projects, newest activity first.

        Powers the sidebar TASKS section + activity overview. ``limit=None``
        returns the unbounded set (activity builds a lookup map by task id).
        """
        stmt = select(TaskRow).where(TaskRow.user_id == user_id).order_by(TaskRow.updated_at.desc())
        if limit is not None:
            stmt = stmt.limit(limit)
        return list((await self._db.execute(stmt)).scalars().all())

    async def list_active(self) -> list[TaskRow]:
        """SYSTEM SWEEP (cross-owner). All ``active`` tasks across every owner —
        startup recovery (VALUZ-RESUME Layer 1) resumes each under its own owner
        (the caller threads ``row.user_id``)."""
        return list(
            (await self._db.execute(select(TaskRow).filter_by(status="active"))).scalars().all()
        )

    # -- Commands --

    async def create_task(self, user_id: str, row: TaskRow) -> TaskRow:
        row.user_id = user_id
        self._db.add(row)
        await async_commit_with_retry(self._db, where="TaskDatastore.create_task")
        return row

    async def update_task(self, row: TaskRow) -> TaskRow:
        await self._db.merge(row)
        await async_commit_with_retry(self._db, where="TaskDatastore.update_task")
        return row

    async def cas_update_plan(
        self, user_id: str, row: TaskRow, plan: dict[str, Any], *, expected_version: int
    ) -> bool:
        """Compare-and-swap plan write: succeeds only at ``expected_version``.

        The plan column is a whole-document JSON write, so without the version
        predicate two concurrent writers (lead loop vs heartbeat vs stop) each
        read-modify-write and the loser's nodes are silently reverted. Every
        plan write goes through here and bumps ``plan_version`` by 1 — the
        version is the write counter, not just the structural-edit counter.

        After the statement the ORM row is refreshed IN PLACE (sessions run
        ``expire_on_commit=False``, so a plain re-select would return the same
        stale identity-mapped object): on success the caller sees the new
        values, on conflict the winner's — ready for a retry loop. Raises if
        the row vanished (task deleted concurrently).

        Returns True when the row was written.
        """
        res = cast(
            "CursorResult[Any]",
            await self._db.execute(
                update(TaskRow)
                .where(
                    TaskRow.id == row.id,
                    TaskRow.user_id == user_id,
                    TaskRow.plan_version == expected_version,
                )
                .values(plan=plan, plan_version=expected_version + 1, updated_at=now_ms())
                .execution_options(synchronize_session=False)
            ),
        )
        await async_commit_with_retry(self._db, where="TaskDatastore.cas_update_plan")
        await self._db.refresh(row)
        return bool(res.rowcount)

    async def _current_status(self, user_id: str, task_id: str) -> str | None:
        """The row's persisted status (seam for the CAS door; tests fake it
        to drive the read→write race deterministically)."""
        return cast(
            "str | None",
            await self._db.scalar(
                select(TaskRow.status).where(TaskRow.id == task_id, TaskRow.user_id == user_id)
            ),
        )

    async def update_task_status(
        self, user_id: str, task_id: str, status: str, *, expect: str | None = None
    ) -> bool:
        """Status door: ``task_state`` machine + a compare-and-swap write.

        Refuses an out-of-enum target (e.g. the legacy ``"failed"``) and an
        illegal transition from a known source. The UPDATE itself carries a
        ``status = <source>`` predicate, so two concurrent writers who both
        read the same source can't both land — without it, a stop_task racing
        an auto-finalize could persist the forbidden net transition
        stopped → blocked and publish ``task.finalized`` twice with
        contradictory statuses. Losing the race returns False (True when the
        winner performed the SAME transition — idempotent), and the caller
        must not proceed with the side effects that ride the flip.

        ``expect``: assert the source status too (``commit_task`` passes
        ``"draft"`` — the flip then doubles as the commit mutex: exactly one
        of two concurrent commits wins the rowcount).

        Tolerances (logged, not raised): a same-status write is a no-op, and a
        legacy/unknown *source* status (a row written before this enforcement)
        is allowed through so it can still be recovered (e.g. → ``active`` on
        resume) instead of being bricked.
        """
        if not is_valid_status(status):
            raise TaskStateError(f"refusing to write invalid task status {status!r}")
        current = await self._current_status(user_id, task_id)
        if current is None:
            return False
        if expect is not None and current != expect:
            return False
        if current == status:
            return True  # no-op
        if is_valid_status(current):
            assert_transition(current, status)  # raises TaskStateError if illegal
        else:
            logger.warning(
                "update_task_status: legacy/unknown source status %r for task %s "
                "→ %r (allowed without transition check)",
                current,
                task_id,
                status,
            )
        # ``AsyncSession.execute`` is typed as returning ``Result``, but a DML
        # statement always yields a ``CursorResult`` — the only shape carrying
        # ``rowcount``. Narrow once here instead of ignoring the error.
        res = cast(
            "CursorResult[Any]",
            await self._db.execute(
                update(TaskRow)
                .where(
                    TaskRow.id == task_id,
                    TaskRow.user_id == user_id,
                    TaskRow.status == current,
                )
                .values(status=status, updated_at=now_ms())
                .execution_options(synchronize_session=False)
            ),
        )
        await async_commit_with_retry(self._db, where="TaskDatastore.update_task_status")
        if res.rowcount:
            return True
        landed = await self._current_status(user_id, task_id)
        if landed == status and expect is None:
            return True  # someone else performed the same transition
        logger.error(
            "update_task_status: lost status race for task %s — read %r, "
            "row now %r, refused writing %r",
            task_id,
            current,
            landed,
            status,
        )
        return False


class TaskEventDatastore:
    """Append-only event log for tasks."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # -- Queries --

    async def list_events(self, user_id: str, project_id: str, task_id: str) -> list[TaskEventRow]:
        return list(
            (
                await self._db.execute(
                    select(TaskEventRow)
                    .where(
                        TaskEventRow.project_id == project_id,
                        TaskEventRow.task_id == task_id,
                        TaskEventRow.user_id == user_id,
                    )
                    .order_by(TaskEventRow.sequence)
                )
            )
            .scalars()
            .all()
        )

    async def list_events_after(
        self,
        user_id: str,
        project_id: str,
        task_id: str,
        after_seq: int,
    ) -> list[TaskEventRow]:
        """Events strictly newer than ``after_seq`` (SSE cursor)."""
        return list(
            (
                await self._db.execute(
                    select(TaskEventRow)
                    .where(
                        TaskEventRow.project_id == project_id,
                        TaskEventRow.task_id == task_id,
                        TaskEventRow.user_id == user_id,
                        TaskEventRow.sequence > after_seq,
                    )
                    .order_by(TaskEventRow.sequence)
                )
            )
            .scalars()
            .all()
        )

    async def latest_event(self, user_id: str, task_id: str) -> TaskEventRow | None:
        """The most recent timeline event for one of the caller's tasks."""
        return (
            await self._db.execute(
                select(TaskEventRow)
                .where(TaskEventRow.task_id == task_id, TaskEventRow.user_id == user_id)
                .order_by(TaskEventRow.sequence.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    # -- Commands --

    async def append_event(
        self,
        user_id: str,
        project_id: str,
        task_id: str,
        type: str,
        actor: str,
        session_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> TaskEventRow:
        """Append an event with a monotonic sequence per (project_id, task_id).

        Retries on the ``(project_id, task_id, sequence)`` unique-collision (a
        loser re-sequences) and on SQLite ``database is locked``.
        """
        last_exc: Exception | None = None
        for attempt in range(_LOCK_RETRY_ATTEMPTS):
            max_seq = (
                await self._db.execute(
                    select(func.max(TaskEventRow.sequence)).filter_by(
                        project_id=project_id, task_id=task_id
                    )
                )
            ).scalar()
            next_seq = (max_seq or 0) + 1
            row = TaskEventRow(
                user_id=user_id,
                project_id=project_id,
                task_id=task_id,
                sequence=next_seq,
                type=type,
                actor=actor,
                session_id=session_id,
                payload=payload or {},
            )
            self._db.add(row)
            try:
                await self._db.commit()
                return row
            except IntegrityError as exc:
                await self._db.rollback()
                last_exc = exc
            except OperationalError as exc:
                await self._db.rollback()
                last_exc = exc
                if "locked" not in str(exc).lower():
                    raise
                logger.warning(
                    "LOCKDIAG[append_event:%s] attempt=%d err=%s",
                    task_id,
                    attempt + 1,
                    str(exc)[:80],
                )
                await _lock_backoff_sleep(attempt)
        raise RuntimeError(
            f"append_event: could not commit event for task {task_id} "
            f"after {_LOCK_RETRY_ATTEMPTS} attempts"
        ) from last_exc


class TaskSessionDatastore:
    """CRUD for valuz_task_session (run index) rows."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # -- Queries --

    async def list_runs(self, user_id: str, task_id: str) -> list[TaskSessionRow]:
        return list(
            (
                await self._db.execute(
                    select(TaskSessionRow)
                    .where(TaskSessionRow.task_id == task_id, TaskSessionRow.user_id == user_id)
                    .order_by(TaskSessionRow.sequence)
                )
            )
            .scalars()
            .all()
        )

    async def list_all(self, user_id: str) -> list[TaskSessionRow]:
        """The caller's run-index rows across all tasks (activity overview)."""
        return list(
            (
                await self._db.execute(
                    select(TaskSessionRow).where(TaskSessionRow.user_id == user_id)
                )
            )
            .scalars()
            .all()
        )

    async def get_task_links_by_session_ids(
        self, user_id: str, session_ids: list[str]
    ) -> dict[str, tuple[str, str, str]]:
        """Map run ``session_id`` → ``(task_id, title, status)`` of its owning task.

        Adds the task id + title to the per-session status join
        so the automation activity log can deep-link to the spawned task (not only
        show its live status). Sessions with no task row are omitted.
        """
        if not session_ids:
            return {}
        rows = (
            await self._db.execute(
                select(
                    TaskSessionRow.session_id,
                    TaskRow.id,
                    TaskRow.title,
                    TaskRow.status,
                )
                .join(TaskRow, TaskRow.id == TaskSessionRow.task_id)
                .where(
                    TaskSessionRow.session_id.in_(session_ids),
                    TaskSessionRow.user_id == user_id,
                )
            )
        ).all()
        return {sid: (tid, title, status) for sid, tid, title, status in rows}

    # Run statuses that will produce no further work. The enum is
    # active | paused | completed | rejected | archived, so this is everything
    # except the two that are still in motion.
    SETTLED_RUN_STATUSES = ("completed", "rejected", "archived")

    async def count_runs_by_tasks(
        self, user_id: str, task_ids: list[str]
    ) -> dict[str, tuple[int, int]]:
        """Map task_id → ``(total_runs, settled_runs)`` in ONE query.

        Replaces a ``list_runs`` per task inside a loop: ``list_tasks`` renders
        progress for up to ``limit`` tasks, which cost that many round trips and
        materialised every run row only to count them.
        """
        if not task_ids:
            return {}
        settled = func.sum(
            case((TaskSessionRow.status.in_(self.SETTLED_RUN_STATUSES), 1), else_=0)
        )
        rows = (
            await self._db.execute(
                select(TaskSessionRow.task_id, func.count(), settled)
                .where(
                    TaskSessionRow.task_id.in_(task_ids),
                    TaskSessionRow.user_id == user_id,
                )
                .group_by(TaskSessionRow.task_id)
            )
        ).all()
        return {tid: (int(total), int(done or 0)) for tid, total, done in rows}

    async def get_run(self, session_id: str) -> TaskSessionRow | None:
        """SYSTEM lookup by the globally-unique kernel ``session_id`` (runner +
        kernel-event finalization). Not a user query — no owner filter."""
        return (
            (await self._db.execute(select(TaskSessionRow).filter_by(session_id=session_id)))
            .scalars()
            .first()
        )

    async def next_sequence(self, task_id: str) -> int:
        """Next run sequence for *task_id* (per-task counter; no owner filter —
        ``task_id`` already scopes it and it returns a number, not rows)."""
        max_seq = (
            await self._db.execute(
                select(func.max(TaskSessionRow.sequence)).filter_by(task_id=task_id)
            )
        ).scalar()
        return (max_seq or 0) + 1

    # -- Commands --

    async def create_run(self, user_id: str, row: TaskSessionRow) -> TaskSessionRow:
        row.user_id = user_id
        self._db.add(row)
        await async_commit_with_retry(self._db, where="TaskSessionDatastore.create_run")
        return row

    async def update_run_by_session(
        self,
        session_id: str,
        status: str,
        result_manifest: dict[str, Any] | None = None,
        ended_at: int | None = None,
    ) -> bool:
        """SYSTEM update by the globally-unique kernel ``session_id`` (kernel-
        event finalization path); no owner filter."""
        updates: dict[str, Any] = {"status": status}
        if result_manifest is not None:
            updates["result_manifest"] = result_manifest
        if ended_at is not None:
            updates["ended_at"] = ended_at

        res = cast(
            "CursorResult[Any]",
            await self._db.execute(
                update(TaskSessionRow)
                .where(TaskSessionRow.session_id == session_id)
                .values(**updates)
            ),
        )
        await async_commit_with_retry(self._db, where="TaskSessionDatastore.update_run_by_session")
        return bool(res.rowcount)

    async def settle_run_if_active(
        self,
        session_id: str,
        *,
        status: str,
        result_manifest: dict[str, Any] | None = None,
        ended_at: int | None = None,
    ) -> bool:
        """Loop-exit settlement, gated on the run still being ``active``.

        A run that is no longer active already had its outcome recorded by
        someone with more context — ``stop_member`` (→rejected, lead notified)
        or ``stop_task`` (→paused, resumable). Overwriting that with the
        loop's own exit status destroys it: recovery only resumes
        active/paused runs, so a parked run stamped ``completed`` goes
        invisible and its node is re-dispatched as a brand-new session.

        Returns False when the run was not active (nothing written).
        """
        updates: dict[str, Any] = {"status": status}
        if result_manifest is not None:
            updates["result_manifest"] = result_manifest
        if ended_at is not None:
            updates["ended_at"] = ended_at
        res = cast(
            "CursorResult[Any]",
            await self._db.execute(
                update(TaskSessionRow)
                .where(
                    TaskSessionRow.session_id == session_id,
                    TaskSessionRow.status == "active",
                )
                .values(**updates)
            ),
        )
        await async_commit_with_retry(self._db, where="TaskSessionDatastore.settle_run_if_active")
        return bool(res.rowcount)
