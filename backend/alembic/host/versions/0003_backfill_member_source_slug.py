"""backfill project members' source_agent_slug

Legacy ``valuz_project_member`` rows minted before provenance landed carry
``source_agent_slug = NULL``. The agent-snapshot cutover resolves a member's
configuration through its source library agent, so backfill the slug by
joining the member's shared ``kernel_agent_id`` back to the library row that
owns it. Members whose kernel id matches no library agent stay NULL and keep
working through the dual-track kernel-row fallback until the agents table is
removed.

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-10

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE valuz_project_member
        SET source_agent_slug = (
            SELECT slug FROM valuz_agent
            WHERE valuz_agent.kernel_agent_id = valuz_project_member.kernel_agent_id
        )
        WHERE source_agent_slug IS NULL
          AND kernel_agent_id IS NOT NULL
        """
    )


def downgrade() -> None:
    # Data-only backfill; the provenance column predates this revision and
    # NULLing it back would discard organically-set values. No-op.
    pass
