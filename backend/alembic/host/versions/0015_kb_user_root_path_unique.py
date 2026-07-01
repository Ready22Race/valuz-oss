"""docs: scope knowledge-base root paths by owner

Knowledge-base reads and duplicate checks already scope ``root_path`` by
``user_id``, but the database still enforced a global unique index. Shared
backends need different users to be able to attach the same resolved root path
independently. Replace the global root-path index with an owner-scoped one.

Downgrading restores global uniqueness. If data already relies on user-scoped
root paths, abort before changing indexes.

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-01

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _assert_no_cross_owner_duplicate_root_paths() -> None:
    duplicates = (
        op.get_bind()
        .execute(
            sa.text(
                """
                SELECT root_path
                FROM valuz_knowledge_base
                GROUP BY root_path
                HAVING COUNT(DISTINCT user_id) > 1
                """
            )
        )
        .fetchall()
    )
    if not duplicates:
        return
    root_paths = ", ".join(str(row[0]) for row in duplicates[:10])
    extra = "" if len(duplicates) <= 10 else f" (+{len(duplicates) - 10} more)"
    raise RuntimeError(
        "Cannot downgrade KB root-path uniqueness while root paths are shared "
        f"across users: {root_paths}{extra}"
    )


def upgrade() -> None:
    op.drop_index("ux_kb_root_path", table_name="valuz_knowledge_base")
    op.create_index(
        "ux_kb_user_root_path",
        "valuz_knowledge_base",
        ["user_id", "root_path"],
        unique=True,
    )


def downgrade() -> None:
    _assert_no_cross_owner_duplicate_root_paths()
    op.drop_index("ux_kb_user_root_path", table_name="valuz_knowledge_base")
    op.create_index(
        "ux_kb_root_path",
        "valuz_knowledge_base",
        ["root_path"],
        unique=True,
    )
