"""automation: add origin_tool_call_id

Adds ``valuz_automation.origin_tool_call_id`` (nullable, indexed) — the kernel
``tool_use`` id of the ``automation create`` call that proposed the row, stamped
when the user confirms the proposal card. NULL for UI-created rows. The index
backs the session-reload lookup that maps historical proposing tool-calls → their
created automations (so a confirmed proposal card shows "already added" instead
of a fresh Confirm button).

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
    with op.batch_alter_table("valuz_automation", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("origin_tool_call_id", sa.String(length=128), nullable=True)
        )
        batch_op.create_index(
            "ix_valuz_automation_origin_tool_call_id",
            ["origin_tool_call_id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("valuz_automation", schema=None) as batch_op:
        batch_op.drop_index("ix_valuz_automation_origin_tool_call_id")
        batch_op.drop_column("origin_tool_call_id")
