"""automation_run: add invoked_by_session_id

Adds ``valuz_automation_run.invoked_by_session_id`` (nullable) — the session that
asked for an agent-invoked run (``automation`` MCP tool, trigger_type="agent").
Lets a task spawned by that run chain its provenance back to the originating
task, so a task→automation→task chain nests in the task tree. NULL for
cron/interval/manual runs.

Revision ID: 0011
Revises: 0010
Create Date: 2026-06-26

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("valuz_automation_run", schema=None) as batch_op:
        batch_op.add_column(sa.Column("invoked_by_session_id", sa.String(length=36), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("valuz_automation_run", schema=None) as batch_op:
        batch_op.drop_column("invoked_by_session_id")
