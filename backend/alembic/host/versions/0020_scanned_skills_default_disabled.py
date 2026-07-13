"""skills: default discovered skills to library disabled

Revision ID: 0020
Revises: 0019
Create Date: 2026-07-10

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MARKETPLACE_TABLE_NAME = "marketplace_install"


def upgrade() -> None:
    _ensure_marketplace_install_table()

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


def _ensure_marketplace_install_table() -> None:
    """Compatibility for dev DBs that already stamped the old local 0019.

    Before this branch merged upstream main, revision ``0019`` meant this
    scanned-skills migration. Upstream main now owns ``0019`` for
    ``marketplace_install``. A local DB that already stamped the old 0019 would
    otherwise skip upstream's marketplace migration, so this revision repairs the
    missing table when needed.
    """
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table(_MARKETPLACE_TABLE_NAME):
        return

    op.create_table(
        _MARKETPLACE_TABLE_NAME,
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("item_id", sa.String(length=255), nullable=False),
        sa.Column("item_type", sa.String(length=32), nullable=False),
        sa.Column("installed_ref", sa.String(length=255), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("auto_update", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source_channel", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("user_id", "item_id", name="uq_marketplace_install_owner_item"),
    )
    op.create_index(
        "ix_marketplace_install_user_id",
        _MARKETPLACE_TABLE_NAME,
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_marketplace_install_item_id",
        _MARKETPLACE_TABLE_NAME,
        ["item_id"],
        unique=False,
    )
    op.create_index(
        "ix_marketplace_install_ref",
        _MARKETPLACE_TABLE_NAME,
        ["installed_ref"],
        unique=False,
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
