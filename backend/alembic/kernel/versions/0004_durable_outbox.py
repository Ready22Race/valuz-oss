"""kernel: durable write-through outbox

Adds the ``durable_outbox`` table to the LOCAL kernel DB. It backs best-effort
write-through (``kernel_store=pg``): when a durable mirror write fails, the op is
queued here so the local write still succeeds and a background drainer re-pushes
it once the durable store recovers. Unused in strict (``remote``) mode.

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-29 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "durable_outbox",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("op", sa.String(length=40), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("body", sa.JSON(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=True),
    )
    op.create_index("ix_durable_outbox_id", "durable_outbox", ["id"])


def downgrade() -> None:
    op.drop_index("ix_durable_outbox_id", table_name="durable_outbox")
    op.drop_table("durable_outbox")
