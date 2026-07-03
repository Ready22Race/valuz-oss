"""skills: add per-owner unique skill slug

Keep the existing ``valuz_skill_index.id`` primary key column, migrate legacy
manifest-derived row ids to generated ids, and add the business uniqueness rule
for one owner's skill slug.

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-03

"""

from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX_NAME = "ux_valuz_skill_index_user_slug"
_TABLE_NAME = "valuz_skill_index"


def _is_generated_row_id(value: str) -> bool:
    return len(value) == 32 and all(char in "0123456789abcdef" for char in value)


def _has_index(index_name: str) -> bool:
    return any(
        index["name"] == index_name
        for index in sa.inspect(op.get_bind()).get_indexes(_TABLE_NAME)
    )


def _assert_no_per_owner_duplicate_slugs() -> None:
    duplicates = (
        op.get_bind()
        .execute(
            sa.text(
                """
                SELECT user_id, slug
                FROM valuz_skill_index
                GROUP BY user_id, slug
                HAVING COUNT(*) > 1
                """
            )
        )
        .fetchall()
    )
    if not duplicates:
        return
    slugs = ", ".join(f"{row[0]}:{row[1]}" for row in duplicates[:10])
    extra = "" if len(duplicates) <= 10 else f" (+{len(duplicates) - 10} more)"
    raise RuntimeError(
        "Cannot add skill-index unique slug constraint while duplicate "
        f"user/slug rows exist: {slugs}{extra}"
    )


def _migrate_legacy_manifest_ids() -> None:
    """Stop legacy rows from keeping manifest ids as primary keys.

    Older index rows used values like ``official:skill-creator`` as
    ``valuz_skill_index.id``. New rows use ``PrimaryKeyMixin`` generated ids and
    bind business identity through ``(user_id, slug)``. There are no foreign-key
    references to this table's id, so rewriting the row primary key preserves
    the useful data while removing the old global collision surface.
    """
    bind = op.get_bind()
    rows = bind.execute(sa.text(f"SELECT id FROM {_TABLE_NAME}")).fetchall()
    used_ids = {str(row[0]) for row in rows}
    for row in rows:
        old_id = str(row[0])
        if _is_generated_row_id(old_id):
            continue
        new_id = uuid4().hex
        while new_id in used_ids:
            new_id = uuid4().hex
        used_ids.add(new_id)
        bind.execute(
            sa.text(f"UPDATE {_TABLE_NAME} SET id = :new_id WHERE id = :old_id"),
            {"new_id": new_id, "old_id": old_id},
        )


def upgrade() -> None:
    if _has_index(_INDEX_NAME):
        return
    _assert_no_per_owner_duplicate_slugs()
    _migrate_legacy_manifest_ids()
    op.create_index(
        _INDEX_NAME,
        _TABLE_NAME,
        ["user_id", "slug"],
        unique=True,
    )


def downgrade() -> None:
    if _has_index(_INDEX_NAME):
        op.drop_index(_INDEX_NAME, table_name=_TABLE_NAME)
