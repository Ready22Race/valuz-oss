"""Project↔session index — module-level service facade.

The host's own mapping of kernel sessions to projects (see
``models.ProjectSessionRow``). Functions here open their own unit of work so
sibling modules (tasks, projects, runs, automations) can call them without
threading a DB session through — cross-module collaboration stays at the
service layer per the module-boundary contract.

Every kernel ``save_session`` **creation** site must be paired with a
``record(...)`` call; updates to existing sessions don't touch the index.
"""

from __future__ import annotations

from sqlalchemy import delete, func, select, update

from valuz_agent.infra.auth_context import require_current_user_id
from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.infra.time_utils import now_ms
from valuz_agent.modules.sessions.models import ProjectSessionRow

__all__ = [
    "count_for_project",
    "get_queue_paused_at",
    "list_recent",
    "list_session_ids",
    "project_of",
    "record",
    "remove",
    "remove_for_project",
    "set_queue_paused",
]


async def record(
    project_id: str,
    session_id: str,
    *,
    kind: str = "chat",
    origin: str = "user",
) -> None:
    """Register a freshly created kernel session under its project.

    Idempotent on ``session_id`` (re-recording an id updates the row) so
    boot-time reconciliation and retries can't violate the unique index.
    """
    user_id = require_current_user_id()
    async with async_unit_of_work() as db:
        existing = (
            (await db.execute(select(ProjectSessionRow).filter_by(session_id=session_id)))
            .scalars()
            .first()
        )
        if existing is not None:
            existing.project_id = project_id
            existing.kind = kind
            existing.origin = origin
            return
        db.add(
            ProjectSessionRow(
                user_id=user_id,
                project_id=project_id,
                session_id=session_id,
                kind=kind,
                origin=origin,
            )
        )


async def list_session_ids(
    project_id: str | None = None,
    *,
    user_only: bool = False,
    kind: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[str]:
    """Session ids, newest first. ``user_only`` keeps conversation kinds
    (``chat``) and drops task-internal runs (lead / subtask). ``kind`` filters
    to one exact ``ProjectSessionRow.kind`` (e.g. ``"task_lead"``) and takes
    precedence over ``user_only`` when both are given."""
    async with async_unit_of_work(commit=False) as db:
        stmt = select(ProjectSessionRow.session_id).where(
            ProjectSessionRow.user_id == require_current_user_id()
        )
        if project_id is not None:
            stmt = stmt.where(ProjectSessionRow.project_id == project_id)
        if kind is not None:
            stmt = stmt.where(ProjectSessionRow.kind == kind)
        elif user_only:
            stmt = stmt.where(ProjectSessionRow.kind == "chat")
        stmt = stmt.order_by(ProjectSessionRow.created_at.desc()).offset(offset).limit(limit)
        return list((await db.execute(stmt)).scalars().all())


async def project_of(session_id: str) -> str | None:
    # SYSTEM lookup by the globally-unique kernel ``session_id`` — returns only
    # the project id; not owner-scoped.
    async with async_unit_of_work(commit=False) as db:
        stmt = select(ProjectSessionRow.project_id).filter_by(session_id=session_id)
        return (await db.execute(stmt)).scalars().first()


async def get_queue_paused_at(session_id: str) -> int | None:
    """Read the input-queue pause marker for a session (SYSTEM, by session_id).

    ``None`` = not paused (drain freely). A timestamp = an interrupt soft-paused
    auto-drain; it stays paused across restart until an explicit resume. See
    docs/design/session-input-queue.md §9.
    """
    async with async_unit_of_work(commit=False) as db:
        stmt = select(ProjectSessionRow.queue_paused_at).filter_by(session_id=session_id)
        return (await db.execute(stmt)).scalars().first()


async def set_queue_paused(session_id: str, paused: bool) -> None:
    """Set/clear the input-queue pause marker (SYSTEM, by session_id)."""
    async with async_unit_of_work() as db:
        await db.execute(
            update(ProjectSessionRow)
            .where(ProjectSessionRow.session_id == session_id)
            .values(queue_paused_at=now_ms() if paused else None)
        )


async def count_for_project(project_id: str) -> int:
    async with async_unit_of_work(commit=False) as db:
        stmt = select(func.count(ProjectSessionRow.id)).where(
            ProjectSessionRow.project_id == project_id,
            ProjectSessionRow.user_id == require_current_user_id(),
        )
        return int((await db.execute(stmt)).scalar() or 0)


async def remove(session_id: str) -> None:
    async with async_unit_of_work() as db:
        await db.execute(
            delete(ProjectSessionRow).where(
                ProjectSessionRow.session_id == session_id,
                ProjectSessionRow.user_id == require_current_user_id(),
            )
        )


async def remove_for_project(project_id: str) -> list[str]:
    """Drop every index row for ``project_id``; returns the removed session
    ids so the caller can cascade the kernel-side deletes."""
    user_id = require_current_user_id()
    async with async_unit_of_work() as db:
        stmt = select(ProjectSessionRow.session_id).where(
            ProjectSessionRow.project_id == project_id,
            ProjectSessionRow.user_id == user_id,
        )
        ids = list((await db.execute(stmt)).scalars().all())
        await db.execute(
            delete(ProjectSessionRow).where(
                ProjectSessionRow.project_id == project_id,
                ProjectSessionRow.user_id == user_id,
            )
        )
        return ids


async def list_recent(limit: int = 200) -> list[ProjectSessionRow]:
    """Most recent index rows for the caller across all their projects — the
    runs-overview feed."""
    async with async_unit_of_work(commit=False) as db:
        stmt = (
            select(ProjectSessionRow)
            .where(ProjectSessionRow.user_id == require_current_user_id())
            .order_by(ProjectSessionRow.created_at.desc())
            .limit(limit)
        )
        return list((await db.execute(stmt)).scalars().all())
