"""drop kernel_agent_id columns

The kernel agents table is gone — sessions embed their agent snapshot and
project members live-reference their source library agent via
``source_agent_slug``. The two ``kernel_agent_id`` columns are dangling
references with no remaining reader.

Reversible structurally: downgrade re-adds both columns as nullable (the
values are not recoverable, matching the snapshot architecture where they
no longer mean anything).

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-10

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
    # Safety net: re-run the 0003 backfill for any member row that gained a
    # kernel_agent_id between migrations (idempotent, same predicate).
    op.execute(
        """
        UPDATE valuz_project_member
        SET source_agent_slug = (
            SELECT slug FROM valuz_agent
            WHERE valuz_agent.kernel_agent_id = valuz_project_member.kernel_agent_id
        )
        WHERE source_agent_slug IS NULL
          AND kernel_agent_id IS NOT NULL
        """
    )
    with op.batch_alter_table("valuz_project_member", schema=None) as batch_op:
        batch_op.drop_column("kernel_agent_id")
    with op.batch_alter_table("valuz_agent", schema=None) as batch_op:
        batch_op.drop_column("kernel_agent_id")


def downgrade() -> None:
    with op.batch_alter_table("valuz_agent", schema=None) as batch_op:
        batch_op.add_column(sa.Column("kernel_agent_id", sa.String(length=36), nullable=True))
    with op.batch_alter_table("valuz_project_member", schema=None) as batch_op:
        batch_op.add_column(sa.Column("kernel_agent_id", sa.String(length=36), nullable=True))
