"""kernel: SaaS-ready store (event_uid idempotency + RLS backstop)

Consolidated schema change for the model-A remote/durable store work. Two
parts, both additive over the 0001 baseline:

1. ``events.event_uid`` (nullable) + UNIQUE ``(user_id, event_uid)`` — at-least-once
   append idempotency: a retried append (same client ``request_id``) conflicts on
   the index and the store returns the original row's ``seq``. NULL (local
   in-process appends) is distinct under the index on SQLite + Postgres, so
   existing local behaviour is unchanged.
2. Row-level security on ``sessions``/``messages``/``events`` (**Postgres only**) —
   an owner-isolation policy reading the ``app.current_user_id`` per-transaction
   GUC the data service sets via ``SET LOCAL`` from the verified token. A DB
   backstop even if an app-layer ``user_id`` filter is ever missed. No-op on
   SQLite (no RLS; OSS local-first relies on app-layer scoping). RLS does NOT
   apply to the table owner (no ``FORCE``) — the data service must connect as a
   NON-owner role for it to take effect.

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-29 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RLS_TABLES = ("sessions", "messages", "events")
_GUC = "app.current_user_id"


def upgrade() -> None:
    # 1. event_uid idempotency key.
    op.add_column("events", sa.Column("event_uid", sa.String(length=64), nullable=True))
    op.create_index("uq_events_owner_uid", "events", ["user_id", "event_uid"], unique=True)

    # 2. Row-level security backstop (Postgres only).
    if op.get_bind().dialect.name == "postgresql":
        for table in _RLS_TABLES:
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            op.execute(
                f"CREATE POLICY {table}_owner_isolation ON {table} "
                f"USING (user_id = current_setting('{_GUC}', true)) "
                f"WITH CHECK (user_id = current_setting('{_GUC}', true))"
            )


def downgrade() -> None:

    if op.get_bind().dialect.name == "postgresql":
        for table in _RLS_TABLES:
            op.execute(f"DROP POLICY IF EXISTS {table}_owner_isolation ON {table}")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.drop_index("uq_events_owner_uid", table_name="events")
    op.drop_column("events", "event_uid")
