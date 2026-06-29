"""kernel: row-level security backstop (Postgres only)

Enables RLS + an owner-isolation policy on sessions/messages/events so the
database enforces owner scoping even if an app-layer ``user_id`` filter is ever
missed. The policy reads ``app.current_user_id`` — a per-transaction GUC the
data service sets via ``SET LOCAL`` from the verified token (see
``app.data_service.install_rls_guc``).

Postgres-only: SQLite has no RLS and OSS local-first relies on app-layer
scoping, so this is a no-op there (guarded by dialect; the SQLite DB is still
stamped at 0003). RLS does NOT apply to the table owner (no ``FORCE``) —
migrations and trusted owner reads stay unrestricted; the data service must
connect as a NON-owner role for the policy to take effect.

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-28 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("sessions", "messages", "events")
_GUC = "app.current_user_id"


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return  # RLS is a Postgres-only backstop; SQLite uses app-layer scoping
    for table in _TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_owner_isolation ON {table} "
            f"USING (user_id = current_setting('{_GUC}', true)) "
            f"WITH CHECK (user_id = current_setting('{_GUC}', true))"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in _TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_owner_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
