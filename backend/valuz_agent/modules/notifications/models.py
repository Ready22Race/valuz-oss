"""Durable notification ledger (docs/design/notifications.md).

One ``valuz_notification`` row per attention item — a question the user must
answer, a task failure they must resume, etc. This is the SINGLE persisted
account of "things needing the user", fed by projectors (question / failure /
…) and fanned out to every delivery surface (in-app badge/drawer/toast + OS
notification + dock bounce). Read/resolved lifecycle lives here, so it survives
restart and distinguishes "notified" from "handled".
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, BigInteger, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from valuz_agent.infra.database import Base, PrimaryKeyMixin, TimestampMixin, UserMixin


class NotificationRow(Base, PrimaryKeyMixin, TimestampMixin, UserMixin):
    """A single attention item. Lifecycle: created → (read) → resolved."""

    __tablename__ = "valuz_notification"

    __table_args__ = (
        # Idempotent ingest: a projector re-firing for the same subject upserts
        # the same row instead of duplicating it.
        UniqueConstraint("user_id", "dedup_key", name="uq_notification_user_dedup"),
        # Badge / drawer queries filter by owner + open state, newest first.
        Index("ix_notification_user_created", "user_id", "created_at"),
    )

    # question | task_failed | task_stalled | task_completed | …
    kind: Mapped[str] = mapped_column(String(32))
    # Stable per-(user) key for idempotent upsert, e.g. ``q:{pending_id}`` /
    # ``f:{event_id}``.
    dedup_key: Mapped[str] = mapped_column(String(128))

    # Snapshot at creation so the drawer/history render without re-joining the
    # source domain rows.
    title: Mapped[str] = mapped_column(String(256))
    body: Mapped[str] = mapped_column(String(2048), default="")
    # In-app deep link opened on click (``/tasks/{id}`` etc.).
    route: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # answer | resume | none — decides which action the drawer renders.
    action: Mapped[str] = mapped_column(String(16), default="none")
    # actionable | info — decides delivery channels (info skips OS popup).
    urgency: Mapped[str] = mapped_column(String(16), default="actionable")

    # Source references — for the action + dedup + reconcile.
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    project_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    pending_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_event_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    # Kind-specific payload (e.g. the AskUserQuestion ``{questions:[…]}`` blob
    # for ``question`` so the answer card renders verbatim).
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    # Lifecycle stamps (ms). ``read_at IS NULL`` → unread; ``resolved_at IS
    # NULL`` → still open (answered / resumed / dismissed clears it).
    read_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    resolved_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
