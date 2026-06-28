"""skill library switch: global per-skill on/off on the index row

Adds ``library_enabled`` to ``valuz_skill_index`` — the global library on/off
for a skill, toggled per (dedup-winning) row from the Skills page. Defaults to
1 so every existing skill stays enabled; ``startup_scan`` preserves the flag
across rescans. Off hides a skill from a new (non-project) conversation's inline
``/`` picker; it never affects runtime loading or an agent's own ``/``.

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
    with op.batch_alter_table("valuz_skill_index", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "library_enabled",
                sa.Boolean(),
                nullable=False,
                # Portable boolean literal: SQLAlchemy renders ``1`` on SQLite
                # (no native boolean) and ``true`` on Postgres. A bare
                # ``text("1")`` renders ``DEFAULT 1`` on Postgres too, which it
                # rejects ("column is of type boolean but default expression is
                # of type integer").
                server_default=sa.true(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("valuz_skill_index", schema=None) as batch_op:
        batch_op.drop_column("library_enabled")
