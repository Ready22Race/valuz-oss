"""automations: rename task_worktree → worktree

Worktree isolation is no longer task-only — a ``chat``-action automation bound
to a git-repo project can now run each fire in its own worktree too
(docs/design/project-worktree-design.md §5). The column is renamed to drop the
misleading ``task_`` prefix now that it applies to both action kinds.

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-06

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("valuz_automation") as batch:
        batch.alter_column("task_worktree", new_column_name="worktree")


def downgrade() -> None:
    with op.batch_alter_table("valuz_automation") as batch:
        batch.alter_column("worktree", new_column_name="task_worktree")
