"""skills: key the index on (user_id, source_path) instead of (user_id, slug)

The skill index business identity is the on-disk skill directory, not the slug.
A bundled ``official`` skill (under the official-skills dir) and a ``user`` copy
of the same slug (under ``~/.agents/skills``) are distinct skills and must
coexist as two rows — the catalog shows each in its own source group. The old
``(user_id, slug)`` unique forced one row per slug, so whichever source scanned
first claimed it and the other was silently dropped.

This migration swaps the unique index to ``(user_id, source_path)``.

Revision ID: 0022
Revises: 0021
Create Date: 2026-07-14

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE_NAME = "valuz_skill_index"
_OLD_INDEX = "ux_valuz_skill_index_user_slug"
_NEW_INDEX = "ux_valuz_skill_index_user_source_path"


def _has_index(index_name: str) -> bool:
    return any(
        index["name"] == index_name for index in sa.inspect(op.get_bind()).get_indexes(_TABLE_NAME)
    )


def _duplicate_groups(*columns: str) -> list[sa.Row]:
    cols = ", ".join(columns)
    return list(
        op.get_bind()
        .execute(
            sa.text(
                f"""
                SELECT {cols}
                FROM {_TABLE_NAME}
                GROUP BY {cols}
                HAVING COUNT(*) > 1
                """
            )
        )
        .fetchall()
    )


def _collapse(where_columns: dict[str, object], *, prefer_official: bool) -> None:
    """Keep one row for a duplicate group, delete the rest.

    ``prefer_official`` keeps an ``official`` row when collapsing a same-slug
    group (downgrade) so the built-in stays visible; otherwise (upgrade, keyed
    on source_path) any survivor is fine since a directory maps to one slug.
    """
    bind = op.get_bind()
    predicate = " AND ".join(f"{col} = :{col}" for col in where_columns)
    rows = list(
        bind.execute(
            sa.text(f"SELECT id, scope FROM {_TABLE_NAME} WHERE {predicate}"),
            where_columns,
        )
        .mappings()
        .all()
    )
    if len(rows) < 2:
        return

    def _rank(row: sa.RowMapping) -> tuple[int, str]:
        official_first = 0 if (prefer_official and row.get("scope") == "official") else 1
        return (official_first, str(row["id"]))

    survivor = sorted(rows, key=_rank)[0]
    bind.execute(
        sa.text(f"DELETE FROM {_TABLE_NAME} WHERE {predicate} AND id != :survivor_id"),
        {**where_columns, "survivor_id": survivor["id"]},
    )


def upgrade() -> None:
    if _has_index(_NEW_INDEX):
        if _has_index(_OLD_INDEX):
            op.drop_index(_OLD_INDEX, table_name=_TABLE_NAME)
        return
    # A skill folder maps to a single slug, so genuine (user_id, source_path)
    # duplicates should not exist — collapse any pathological ones defensively so
    # the unique index can be created.
    for group in _duplicate_groups("user_id", "source_path"):
        _collapse({"user_id": group[0], "source_path": group[1]}, prefer_official=False)
    if _has_index(_OLD_INDEX):
        op.drop_index(_OLD_INDEX, table_name=_TABLE_NAME)
    op.create_index(_NEW_INDEX, _TABLE_NAME, ["user_id", "source_path"], unique=True)


def downgrade() -> None:
    if _has_index(_OLD_INDEX):
        if _has_index(_NEW_INDEX):
            op.drop_index(_NEW_INDEX, table_name=_TABLE_NAME)
        return
    # Coexisting same-slug rows are legal under the new index but violate the old
    # ``(user_id, slug)`` unique — collapse them (keeping the official copy) so
    # the old index can be recreated.
    for group in _duplicate_groups("user_id", "slug"):
        _collapse({"user_id": group[0], "slug": group[1]}, prefer_official=True)
    if _has_index(_NEW_INDEX):
        op.drop_index(_NEW_INDEX, table_name=_TABLE_NAME)
    op.create_index(_OLD_INDEX, _TABLE_NAME, ["user_id", "slug"], unique=True)
