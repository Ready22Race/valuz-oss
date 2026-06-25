"""session input queue: queued follow-up messages + per-session pause marker

Adds ``valuz_queued_input`` — user follow-up messages submitted while a turn is
running, drained FIFO by the host after the active turn completes (host-driven,
budget-checked, durable across restart). Also adds ``queue_paused_at`` to
``valuz_project_session`` so an interrupt can soft-pause auto-drain (resumed
explicitly by the user). See docs/design/session-input-queue.md.

On a fresh / shared backend the table is simply created empty and the new
column defaults to NULL (not paused).

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-24

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "valuz_queued_input",
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("input", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("provider_id", sa.String(length=36), nullable=True),
        sa.Column("model_id", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("valuz_queued_input", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_valuz_queued_input_session_id"), ["session_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_valuz_queued_input_project_id"), ["project_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_valuz_queued_input_status"), ["status"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_valuz_queued_input_user_id"), ["user_id"], unique=False
        )

    with op.batch_alter_table("valuz_project_session", schema=None) as batch_op:
        batch_op.add_column(sa.Column("queue_paused_at", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("valuz_project_session", schema=None) as batch_op:
        batch_op.drop_column("queue_paused_at")

    with op.batch_alter_table("valuz_queued_input", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_valuz_queued_input_user_id"))
        batch_op.drop_index(batch_op.f("ix_valuz_queued_input_status"))
        batch_op.drop_index(batch_op.f("ix_valuz_queued_input_project_id"))
        batch_op.drop_index(batch_op.f("ix_valuz_queued_input_session_id"))
    op.drop_table("valuz_queued_input")
