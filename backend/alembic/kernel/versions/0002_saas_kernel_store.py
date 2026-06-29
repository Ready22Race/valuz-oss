"""kernel: events.event_uid idempotency key

Adds a nullable ``event_uid`` to ``events`` plus a UNIQUE index on
``(user_id, event_uid)``. This enables at-least-once REMOTE append idempotency:
a retried append (same client ``request_id``) conflicts on the unique index and
the store returns the original row's ``seq`` instead of inserting a duplicate.

NULL ``event_uid`` (local in-process appends) is distinct under the index on
both SQLite and PostgreSQL, so existing local behaviour is unchanged.

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-28 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("events", sa.Column("event_uid", sa.String(length=64), nullable=True))
    op.create_index(
        "uq_events_owner_uid", "events", ["user_id", "event_uid"], unique=True
    )


def downgrade() -> None:
    op.drop_index("uq_events_owner_uid", table_name="events")
    op.drop_column("events", "event_uid")
