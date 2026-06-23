"""session artifacts: agent-delivered deliverables (生成文件)

Adds ``valuz_session_artifact`` — the durable list of files an agent declares
as finished outputs via the built-in ``deliver_artifacts`` MCP tool. Distinct
from ``valuz_session_attachment`` (per-turn user *uploads*); this is the
inverse curated "生成文件" list the session panel renders.

On a fresh / shared backend the table is simply created empty.

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-23

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "valuz_session_artifact",
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("file_name", sa.String(length=512), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("mime_type", sa.String(length=128), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("valuz_session_artifact", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_valuz_session_artifact_session_id"), ["session_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_valuz_session_artifact_user_id"), ["user_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("valuz_session_artifact", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_valuz_session_artifact_user_id"))
        batch_op.drop_index(batch_op.f("ix_valuz_session_artifact_session_id"))
    op.drop_table("valuz_session_artifact")
