"""skill library state: per-user global on/off switch for a skill (by slug)

Adds ``valuz_skill_library_state`` — one row per (user, slug) recording the
global library switch for a skill, independent of any project and of the
scan-rebuilt ``valuz_skill_index`` rows. Absence of a row means enabled, so
existing skills need no backfill.

On a fresh / shared backend the table is simply created empty.

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-24

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "valuz_skill_library_state",
        sa.Column("slug", sa.String(length=256), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "slug"),
    )
    with op.batch_alter_table("valuz_skill_library_state", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_valuz_skill_library_state_user_id"), ["user_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("valuz_skill_library_state", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_valuz_skill_library_state_user_id"))
    op.drop_table("valuz_skill_library_state")
