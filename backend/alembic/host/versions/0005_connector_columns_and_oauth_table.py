"""connector: promote args/oauth_metadata to columns + split oauth credentials

Two related moves out of the sparse ``valuz_connector_attr`` KV table (both
partially revert rev 0004), kept in one migration:

1. **Non-secret blobs → columns.** ``args`` (stdio launch args) and
   ``oauth_metadata`` (discovered OAuth server endpoints) carry no credentials
   and belong to the connector definition, so they move back onto the
   ``valuz_connector`` row as plain ``Text`` columns ``args`` / ``oauth_metadata``
   (rev 0001 shipped them as ``args_json`` / ``oauth_metadata_json``).

2. **OAuth credentials → dedicated table.** The per-connector OAuth secrets
   (``oauth_client_info`` / ``oauth_token`` / ``oauth_token_expires_at``) move
   into a dedicated 1:1 ``valuz_connector_oauth`` table as ``client_info`` /
   ``token`` / ``expires_at``, with ``expires_at`` an INDEXED scalar so a
   scheduled token refresher can cheaply query tokens nearing expiry.

What stays in ``valuz_connector_attr``: header/param creds (``headers`` /
``params``) and stdio ``env``.

On a fresh / shared backend there are no rows, so the data steps are no-ops.

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-22

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# attr keys promoted to same-named columns on ``valuz_connector`` (no _json).
_COL_KEYS = ("oauth_metadata", "args")

# attr key → column on the new ``valuz_connector_oauth`` table.
_OAUTH_KEY_TO_COL = {
    "oauth_client_info": "client_info",
    "oauth_token": "token",
    "oauth_token_expires_at": "expires_at",
}


def upgrade() -> None:
    bind = op.get_bind()

    # ── 1. promote the two non-secret blobs to columns ────────────────────
    with op.batch_alter_table("valuz_connector", schema=None) as batch_op:
        batch_op.add_column(sa.Column("oauth_metadata", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("args", sa.Text(), nullable=True))

    rows = bind.execute(
        sa.text(
            "SELECT connector_id, key, value FROM valuz_connector_attr "
            "WHERE key IN ('oauth_metadata', 'args')"
        )
    ).fetchall()
    for connector_id, key, value in rows:
        # ``key`` is constrained to _COL_KEYS by the WHERE clause, so it is a
        # safe identifier equal to the destination column name.
        bind.execute(
            sa.text(f"UPDATE valuz_connector SET {key} = :v WHERE id = :c"),
            {"v": value, "c": connector_id},
        )
    bind.execute(
        sa.text("DELETE FROM valuz_connector_attr WHERE key IN ('oauth_metadata', 'args')")
    )

    # ── 2. split OAuth credentials into a dedicated 1:1 table ─────────────
    op.create_table(
        "valuz_connector_oauth",
        sa.Column("connector_id", sa.String(length=36), nullable=False),
        sa.Column("client_info", sa.Text(), nullable=True),
        sa.Column("token", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.BigInteger(), nullable=True),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("connector_id"),
    )
    op.create_index(op.f("ix_valuz_connector_oauth_user_id"), "valuz_connector_oauth", ["user_id"])
    op.create_index(
        op.f("ix_valuz_connector_oauth_expires_at"),
        "valuz_connector_oauth",
        ["expires_at"],
    )

    oauth_rows = bind.execute(
        sa.text(
            "SELECT connector_id, key, value, user_id FROM valuz_connector_attr "
            "WHERE key IN ('oauth_client_info', 'oauth_token', 'oauth_token_expires_at')"
        )
    ).fetchall()
    grouped: dict[str, dict] = {}
    for connector_id, key, value, user_id in oauth_rows:
        rec = grouped.setdefault(
            connector_id,
            {"client_info": None, "token": None, "expires_at": None, "user_id": user_id},
        )
        rec["user_id"] = user_id
        col = _OAUTH_KEY_TO_COL[key]
        if col == "expires_at":
            rec[col] = (
                int(value) if value is not None and str(value).lstrip("-").isdigit() else None
            )
        else:
            rec[col] = value
    for connector_id, rec in grouped.items():
        bind.execute(
            sa.text(
                "INSERT INTO valuz_connector_oauth "
                "(connector_id, client_info, token, expires_at, user_id) "
                "VALUES (:c, :ci, :t, :e, :u)"
            ),
            {
                "c": connector_id,
                "ci": rec["client_info"],
                "t": rec["token"],
                "e": rec["expires_at"],
                "u": rec["user_id"],
            },
        )
    bind.execute(
        sa.text(
            "DELETE FROM valuz_connector_attr WHERE key IN "
            "('oauth_client_info', 'oauth_token', 'oauth_token_expires_at')"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()

    # ── reverse 2. copy oauth rows back into the attr table, drop table ───
    oauth_rows = (
        bind.execute(
            sa.text(
                "SELECT connector_id, client_info, token, expires_at, user_id "
                "FROM valuz_connector_oauth"
            )
        )
        .mappings()
        .all()
    )
    col_to_key = {col: key for key, col in _OAUTH_KEY_TO_COL.items()}
    for r in oauth_rows:
        for col in ("client_info", "token", "expires_at"):
            val = r[col]
            if val is None:
                continue
            bind.execute(
                sa.text(
                    "INSERT INTO valuz_connector_attr (connector_id, key, value, user_id) "
                    "VALUES (:c, :k, :v, :u)"
                ),
                {"c": r["connector_id"], "k": col_to_key[col], "v": str(val), "u": r["user_id"]},
            )
    op.drop_index(op.f("ix_valuz_connector_oauth_expires_at"), table_name="valuz_connector_oauth")
    op.drop_index(op.f("ix_valuz_connector_oauth_user_id"), table_name="valuz_connector_oauth")
    op.drop_table("valuz_connector_oauth")

    # ── reverse 1. copy the column values back into attr, drop columns ────
    rows = (
        bind.execute(sa.text("SELECT id, user_id, oauth_metadata, args FROM valuz_connector"))
        .mappings()
        .all()
    )
    for r in rows:
        for key in _COL_KEYS:
            val = r[key]
            if val is None:
                continue
            bind.execute(
                sa.text(
                    "INSERT INTO valuz_connector_attr (connector_id, key, value, user_id) "
                    "VALUES (:c, :k, :v, :u)"
                ),
                {"c": r["id"], "k": key, "v": str(val), "u": r["user_id"]},
            )
    with op.batch_alter_table("valuz_connector", schema=None) as batch_op:
        batch_op.drop_column("args")
        batch_op.drop_column("oauth_metadata")
