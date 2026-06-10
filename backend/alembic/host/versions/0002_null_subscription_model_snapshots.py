"""NULL the subscription rows' model_ids — recommended lists go live.

The OAuth subscription rows (``ch-claude-subscription`` /
``ch-codex-subscription``) used to receive a seed-time snapshot of the
recommended model list, which then shadowed every later
``subscription_models.json`` update (``_resolve_model_options`` prefers the
stored list) — surfacing as e.g. claude-fable-5 missing from the picker on
installs seeded before the catalog bump. The seeder no longer snapshots
these rows; this migration brings pre-existing DBs onto the same semantics:
``model_ids IS NULL`` = "track the live recommended catalog from the
descriptor".

Unconditional NULL is safe here: subscription rows have no user write path
for ``model_ids`` (``update_provider`` only accepts model edits for the
``compatible`` kind; ``discover_models`` requires an api_key secret), so the
stored value can only ever be a stale seed snapshot.

Downgrade restores the bundled catalog as of this revision, returning the
rows to snapshot semantics.

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-10

"""

import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

# Bundled recommended lists as of this revision — used only by downgrade()
# to restore the pre-live snapshot state. Deliberately frozen constants:
# a migration must not read the live JSON, or replaying history would
# depend on the current code's catalog.
_SNAPSHOTS: dict[str, list[str]] = {
    "ch-claude-subscription": [
        "claude-fable-5",
        "claude-opus-4-8",
        "claude-opus-4-7",
        "claude-opus-4-6",
        "claude-sonnet-4-6",
        "claude-haiku-4-5",
    ],
    "ch-codex-subscription": [
        "gpt-5.5",
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5.3-codex",
        "gpt-5.3-codex-spark",
        "gpt-5.2",
    ],
}


def upgrade() -> None:
    conn = op.get_bind()
    for row_id in _SNAPSHOTS:
        conn.execute(
            sa.text("UPDATE valuz_provider SET model_ids = NULL WHERE id = :id"),
            {"id": row_id},
        )


def downgrade() -> None:
    conn = op.get_bind()
    for row_id, models in _SNAPSHOTS.items():
        # Only restore rows that are actually in the live-catalog (NULL)
        # state; a non-NULL value here would have to be a post-downgrade
        # edit we must not clobber.
        conn.execute(
            sa.text(
                "UPDATE valuz_provider SET model_ids = :ids "
                "WHERE id = :id AND model_ids IS NULL"
            ),
            {"ids": json.dumps(models), "id": row_id},
        )
