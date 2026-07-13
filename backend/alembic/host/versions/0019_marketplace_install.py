"""marketplace: add marketplace_install provenance table

Records what a user installed from the market index (item id / type /
local ref / version / content hash for skills) — write-only in this phase,
see ``docs/cloud-marketplace/design/oss.md``.

Revision ID: 0019
Revises: 0018
Create Date: 2026-07-11

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE_NAME = "marketplace_install"


def upgrade() -> None:
    op.create_table(
        _TABLE_NAME,
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
        "ix_marketplace_install_user_id", _TABLE_NAME, ["user_id"], unique=False
    )
    op.create_index(
        "ix_marketplace_install_item_id", _TABLE_NAME, ["item_id"], unique=False
    )
    op.create_index(
        "ix_marketplace_install_ref", _TABLE_NAME, ["installed_ref"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_marketplace_install_ref", table_name=_TABLE_NAME)
    op.drop_index("ix_marketplace_install_item_id", table_name=_TABLE_NAME)
    op.drop_index("ix_marketplace_install_user_id", table_name=_TABLE_NAME)
    op.drop_table(_TABLE_NAME)
