"""task: add trigger provenance columns

Adds the "who/what spawned this task" provenance to ``valuz_task``, resolved
once at kickoff/draft and immutable after:

- ``trigger_type``  — user | chat | agent | automation (server_default 'user',
  so existing rows backfill to a sane value).
- ``trigger_task_id``       — parent task for an agent-triggered task (indexed
  for the reverse "what did task X spawn?" lookup).
- ``trigger_agent_slug``    — the agent that triggered it (label).
- ``trigger_automation_id`` — the automation that fired it (indexed for the
  reverse "what did automation X spawn?" lookup).

The originating session (the chat-link source) keeps living on
``metadata.originating_session_id`` — it is load-bearing for the plan-writer
gate — so it is not duplicated here.

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-26

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("valuz_task", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "trigger_type",
                sa.String(length=32),
                nullable=False,
                server_default="user",
            )
        )
        batch_op.add_column(sa.Column("trigger_task_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("trigger_agent_slug", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("trigger_automation_id", sa.String(length=36), nullable=True))
        batch_op.create_index("ix_valuz_task_trigger_task_id", ["trigger_task_id"], unique=False)
        batch_op.create_index(
            "ix_valuz_task_trigger_automation_id", ["trigger_automation_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("valuz_task", schema=None) as batch_op:
        batch_op.drop_index("ix_valuz_task_trigger_automation_id")
        batch_op.drop_index("ix_valuz_task_trigger_task_id")
        batch_op.drop_column("trigger_automation_id")
        batch_op.drop_column("trigger_agent_slug")
        batch_op.drop_column("trigger_task_id")
        batch_op.drop_column("trigger_type")
