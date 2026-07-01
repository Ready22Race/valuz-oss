"""agent: scope slug uniqueness by owner

Agent reads and writes already scope slugs by ``user_id`` in the datastore, but
the database still enforced ``UNIQUE(slug)`` globally. Shared backends need two
users to be able to create or import the same agent slug independently. Replace
the global constraint with ``UNIQUE(user_id, slug)``.

Downgrading restores global uniqueness. If data already relies on user-scoped
slugs, abort before rebuilding tables / creating constraints.

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-01

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_COLUMNS = (
    "slug",
    "name",
    "description",
    "instructions",
    "runtime",
    "model",
    "skills",
    "connector_types",
    "provider_id",
    "effort",
    "source",
    "readonly",
    "deletable",
    "avatar",
    "id",
    "created_at",
    "updated_at",
    "user_id",
)


def _agent_columns() -> list[sa.Column]:
    return [
        sa.Column("slug", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column("runtime", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("skills", sa.JSON(), nullable=False),
        sa.Column("connector_types", sa.JSON(), nullable=False),
        sa.Column("provider_id", sa.String(length=64), nullable=True),
        sa.Column("effort", sa.String(length=16), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("readonly", sa.Boolean(), nullable=False),
        sa.Column("deletable", sa.Boolean(), nullable=False),
        sa.Column("avatar", sa.String(length=128), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
    ]


def _copy_sql(dst: str, src: str) -> sa.TextClause:
    cols = ", ".join(_COLUMNS)
    return sa.text(f"INSERT INTO {dst} ({cols}) SELECT {cols} FROM {src}")


def _rebuild_sqlite_agent(unique: sa.UniqueConstraint) -> None:
    tmp = "valuz_agent__tmp_0014"
    op.create_table(
        tmp,
        *_agent_columns(),
        sa.PrimaryKeyConstraint("id"),
        unique,
    )
    op.execute(_copy_sql(tmp, "valuz_agent"))
    op.drop_table("valuz_agent")
    op.rename_table(tmp, "valuz_agent")
    op.create_index(op.f("ix_valuz_agent_user_id"), "valuz_agent", ["user_id"])


def _find_unique_name(columns: list[str]) -> str:
    bind = op.get_bind()
    for constraint in sa.inspect(bind).get_unique_constraints("valuz_agent"):
        if constraint.get("column_names") == columns and constraint.get("name"):
            return str(constraint["name"])
    raise RuntimeError(f"Could not find valuz_agent unique constraint for {columns!r}")


def _assert_no_cross_owner_duplicate_slugs() -> None:
    duplicates = (
        op.get_bind()
        .execute(
            sa.text("SELECT slug FROM valuz_agent GROUP BY slug HAVING COUNT(DISTINCT user_id) > 1")
        )
        .fetchall()
    )
    if not duplicates:
        return
    slugs = ", ".join(str(row[0]) for row in duplicates[:10])
    extra = "" if len(duplicates) <= 10 else f" (+{len(duplicates) - 10} more)"
    raise RuntimeError(
        "Cannot downgrade agent slug uniqueness while slugs are shared "
        f"across users: {slugs}{extra}"
    )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        _rebuild_sqlite_agent(
            sa.UniqueConstraint("user_id", "slug", name="uq_valuz_agent_user_slug")
        )
        return

    op.drop_constraint(_find_unique_name(["slug"]), "valuz_agent", type_="unique")
    op.create_unique_constraint("uq_valuz_agent_user_slug", "valuz_agent", ["user_id", "slug"])


def downgrade() -> None:
    bind = op.get_bind()
    _assert_no_cross_owner_duplicate_slugs()
    if bind.dialect.name == "sqlite":
        _rebuild_sqlite_agent(sa.UniqueConstraint("slug"))
        return

    op.drop_constraint("uq_valuz_agent_user_slug", "valuz_agent", type_="unique")
    op.create_unique_constraint("uq_valuz_agent_slug", "valuz_agent", ["slug"])
