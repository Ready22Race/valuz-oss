"""skills: default discovered skills to library disabled

Revision ID: 0019
Revises: 0018
Create Date: 2026-07-10

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("valuz_skill_index") as batch:
        batch.alter_column(
            "library_enabled",
            existing_type=sa.Boolean(),
            existing_nullable=False,
            server_default=sa.false(),
        )

    op.execute(
        sa.text(
            """
            UPDATE valuz_skill_index
            SET library_enabled = false
            WHERE scope != 'official'
              AND COALESCE(creation_origin, 'discovered') = 'discovered'
            """
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("valuz_skill_index") as batch:
        batch.alter_column(
            "library_enabled",
            existing_type=sa.Boolean(),
            existing_nullable=False,
            server_default=sa.true(),
        )

    op.execute(
        sa.text(
            """
            UPDATE valuz_skill_index
            SET library_enabled = true
            WHERE scope != 'official'
              AND COALESCE(creation_origin, 'discovered') = 'discovered'
            """
        )
    )
