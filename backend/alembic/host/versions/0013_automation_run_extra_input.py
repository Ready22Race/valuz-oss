"""automation run: add extra_input

Adds ``valuz_automation_run.extra_input`` — optional per-run text appended to
the rendered prompt for a single run only (not stored on the automation). Set
when an agent fires the ``automation`` tool's ``run`` action with an ``input``
argument (e.g. a triage agent passing a discovered task id into a manual
automation's instruction). NULL for plain scheduled / "Run now" runs.

Revision ID: 0013
Revises: 0012
Create Date: 2026-06-30

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("valuz_automation_run", schema=None) as batch_op:
        batch_op.add_column(sa.Column("extra_input", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("valuz_automation_run", schema=None) as batch_op:
        batch_op.drop_column("extra_input")
