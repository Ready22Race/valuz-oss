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
        index["name"] == index_name for index in sa.inspect(op.get_bind()).get_indexes(_TABLE_NAME)
    )


def _table_columns() -> set[str]:
    return {str(column["name"]) for column in sa.inspect(op.get_bind()).get_columns(_TABLE_NAME)}


def _duplicate_slug_groups() -> list[sa.Row]:
    return list(
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


def _pick_duplicate_survivor(rows: list[sa.RowMapping]) -> sa.RowMapping:
    """Choose the row that best matches the post-migration slug upsert behavior."""

    def _rank(row: sa.RowMapping) -> tuple[int, int, int, int, str]:
        scope_rank = {"user": 0, "official": 1, "tenant": 2, "project": 3}
        status_rank = 0 if row.get("status") == "available" else 1
        readonly_rank = 1 if row.get("readonly") else 0
        updated_at = int(row.get("updated_at") or row.get("created_at") or 0)
        return (
            status_rank,
            scope_rank.get(str(row.get("scope") or ""), 9),
            readonly_rank,
            -updated_at,
            str(row["id"]),
        )

    return sorted(rows, key=_rank)[0]


def _merge_duplicate_state(
    rows: list[sa.RowMapping], survivor: sa.RowMapping, columns: set[str]
) -> dict[str, object]:
    updates: dict[str, object] = {}
    if "library_enabled" in columns:
        values = [row.get("library_enabled") for row in rows]
        # A disabled slug is user intent; keep it disabled after collapsing rows.
        updates["library_enabled"] = all(value is not False and value != 0 for value in values)

    if "creation_origin" in columns and not survivor.get("creation_origin"):
        for row in rows:
            if row.get("creation_origin"):
                updates["creation_origin"] = row["creation_origin"]
                break

    if "origin_json" in columns and not survivor.get("origin_json"):
        for row in rows:
            if row.get("origin_json"):
                updates["origin_json"] = row["origin_json"]
                break

    return updates


def _deduplicate_per_owner_slugs() -> None:
    """Collapse legacy duplicate rows before adding ``UNIQUE(user_id, slug)``.

    Older scans could leave multiple index rows for the same owner-visible skill.
    The runtime now treats ``(user_id, slug)`` as the business identity and
    upserts a single row, so migration should repair that historical shape
    instead of leaving affected users unable to start the app.
    """
    bind = op.get_bind()
    columns = _table_columns()
    selected_columns = ", ".join(columns)
    for group in _duplicate_slug_groups():
        rows = list(
            bind.execute(
                sa.text(
                    f"""
                    SELECT {selected_columns}
                    FROM {_TABLE_NAME}
                    WHERE user_id = :user_id AND slug = :slug
                    """
                ),
                {"user_id": group[0], "slug": group[1]},
            )
            .mappings()
            .all()
        )
        if len(rows) < 2:
            continue

        survivor = _pick_duplicate_survivor(rows)
        updates = _merge_duplicate_state(rows, survivor, columns)
        if updates:
            assignments = ", ".join(f"{column} = :{column}" for column in updates)
            bind.execute(
                sa.text(f"UPDATE {_TABLE_NAME} SET {assignments} WHERE id = :id"),
                {**updates, "id": survivor["id"]},
            )
        bind.execute(
            sa.text(
                f"""
                DELETE FROM {_TABLE_NAME}
                WHERE user_id = :user_id AND slug = :slug AND id != :id
                """
            ),
            {"user_id": group[0], "slug": group[1], "id": survivor["id"]},
        )


def upgrade() -> None:
    if _has_index(_INDEX_NAME):
        return
    _deduplicate_per_owner_slugs()
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
