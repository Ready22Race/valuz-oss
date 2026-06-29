"""automation run: add error_message_key

Adds ``valuz_automation_run.error_message_key`` — an optional i18n key the
client renders for a friendly failure message (e.g. a billing rejection),
preferred over the raw ``error_code`` / ``error_message``.

First incremental host migration (the chain was single-baseline before): a DB
stamped at 0002 upgrades in place via ``alembic upgrade head`` without dropping
data — see ``valuz_agent.boot.schema``.

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-16

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("valuz_automation_run", schema=None) as batch_op:
        batch_op.add_column(sa.Column("error_message_key", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("valuz_automation_run", schema=None) as batch_op:
        batch_op.drop_column("error_message_key")
