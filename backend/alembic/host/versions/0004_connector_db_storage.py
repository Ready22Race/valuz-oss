"""connector data → DB (project selection + unified creds + attr KV table)

Moves the connector module's filesystem stores into the DB so a shared
multi-client backend — which has no per-user local filesystem — persists them
like everything else, in a single step from rev 0003:

- ``valuz_project_connector`` — per-project enabled connector slugs, formerly
  ``<project>/.claude/project-config.json`` (the ``connectors`` key; the boot
  backfill imports the legacy files).
- ``valuz_connector_attr`` — the connector's sparse/optional blob attributes as
  a ``connector_id → key → value`` side table (one row per present attribute),
  keeping the main ``valuz_connector`` row lean. The keys are ``oauth_metadata``,
  ``oauth_client_info``, ``oauth_token``, ``oauth_token_expires_at``, ``args``,
  ``env``, ``headers``, ``params`` (no ``_json`` suffix). There is **no**
  DB-level ForeignKey (matching the other ``valuz_*`` tables); the row carries
  the owner ``user_id``.

The credential model is unified along the way: ``headers`` / ``params`` become
``{name: {"value", "secret"}}`` (plaintext + secret together), merging any
legacy ``FileSecretStore`` secret values referenced by the now-dropped
``cred_manifest_json``. The OAuth token (read from the FileSecretStore) lands in
the ``oauth_token`` / ``oauth_token_expires_at`` attrs. The transient PKCE
handoff is NOT stored — it lives in ``ext.cache`` (file locally, Redis on the
shared backend).

On a fresh / shared backend there are no rows, so the data step is a no-op.
A DB stamped at 0003 upgrades in place via ``alembic upgrade head`` without
dropping data — see ``valuz_agent.boot.schema``.

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-21

"""

import json
from collections.abc import Callable, Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Blob columns on ``valuz_connector`` (rev 0003) that move into the
# ``valuz_connector_attr`` KV table, mapped to their (suffix-free) attr key.
# ``cred_manifest_json`` is consumed (its secrets merged into headers/params)
# and dropped — it is NOT stored as an attr.
_COLUMN_TO_KEY = {
    "oauth_metadata_json": "oauth_metadata",
    "oauth_client_info_json": "oauth_client_info",
    "args_json": "args",
    "env_json": "env",
    "headers_json": "headers",
    "params_json": "params",
}


def _manifest_entries(raw: str | None) -> list[dict[str, str]]:
    """Parse a legacy ``cred_manifest_json`` → ``[{target, name, secret_ref}]``."""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    out: list[dict[str, str]] = []
    for m in parsed:
        if (
            isinstance(m, dict)
            and {"target", "name", "secret_ref"} <= set(m)
            and m["target"] in ("header", "param")
        ):
            out.append({k: str(m[k]) for k in ("target", "name", "secret_ref")})
    return out


def _unify_creds(
    headers_json: str | None,
    params_json: str | None,
    manifest_json: str | None,
    read_secret: Callable[[str], str | None],
) -> tuple[str | None, str | None]:
    """Build unified ``{name: {"value", "secret"}}`` for header + param targets.

    Existing plaintext entries (``{name: value}``) become ``secret: false``;
    every ``cred_manifest_json`` secret entry pulls its value via ``read_secret``
    and lands as ``secret: true``. Pure — the migration injects a FileSecretStore
    reader; tests inject a dict.
    """

    def _base(raw: str | None) -> dict[str, dict]:
        out: dict[str, dict] = {}
        if not raw:
            return out
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return out
        if not isinstance(parsed, dict):
            return out
        for k, v in parsed.items():
            if isinstance(v, str):
                out[str(k)] = {"value": v, "secret": False}
            elif isinstance(v, dict) and isinstance(v.get("value"), str):
                out[str(k)] = {"value": v["value"], "secret": bool(v.get("secret", False))}
        return out

    headers = _base(headers_json)
    params = _base(params_json)
    for m in _manifest_entries(manifest_json):
        val = read_secret(m["secret_ref"])
        if val is None:
            continue
        (headers if m["target"] == "header" else params)[m["name"]] = {
            "value": val,
            "secret": True,
        }
    return (
        json.dumps(headers) if headers else None,
        json.dumps(params) if params else None,
    )


def _file_secret_reader() -> Callable[[str], str | None]:
    """A ``ref → value`` reader over the legacy FileSecretStore dir (or a no-op
    reader when it's absent — fresh install / shared backend)."""
    try:
        from valuz_agent.infra.config import settings

        base = settings.secrets_dir
    except Exception:
        base = None

    def _read(ref: str) -> str | None:
        if base is None or not base.is_dir():
            return None
        p = base / ref.replace("/", "__").replace("\\", "__")
        try:
            return p.read_text(encoding="utf-8").strip() if p.is_file() else None
        except OSError:
            return None

    return _read


def _extract_connector_attrs() -> None:
    """Read each connector's blob columns + FileSecretStore secrets and write
    them into ``valuz_connector_attr`` (key without the ``_json`` suffix),
    unifying plaintext + secret creds. No-op on a fresh / shared backend."""
    bind = op.get_bind()
    read_secret = _file_secret_reader()
    cols = ", ".join(_COLUMN_TO_KEY)
    rows = (
        bind.execute(
            sa.text(f"SELECT id, user_id, {cols}, cred_manifest_json FROM valuz_connector")
        )
        .mappings()
        .all()
    )
    for r in rows:
        headers, params = _unify_creds(
            r["headers_json"], r["params_json"], r["cred_manifest_json"], read_secret
        )
        token = read_secret(f"connector/{r['id']}/oauth_token")
        expiry = read_secret(f"connector/{r['id']}/oauth_token_expires_at")
        if not (expiry and expiry.lstrip("-").isdigit()):
            expiry = None
        attrs = {
            "oauth_metadata": r["oauth_metadata_json"],
            "oauth_client_info": r["oauth_client_info_json"],
            "oauth_token": token,
            "oauth_token_expires_at": expiry,
            "args": r["args_json"],
            "env": r["env_json"],
            "headers": headers,
            "params": params,
        }
        for key, val in attrs.items():
            if val is None:
                continue
            bind.execute(
                sa.text(
                    "INSERT INTO valuz_connector_attr (connector_id, key, value, user_id) "
                    "VALUES (:c, :k, :v, :u)"
                ),
                {"c": r["id"], "k": key, "v": str(val), "u": r["user_id"]},
            )


def upgrade() -> None:
    # 1. per-project connector selection (formerly project-config.json)
    op.create_table(
        "valuz_project_connector",
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("slug", sa.String(length=128), nullable=False),
        sa.Column("added_at", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("project_id", "slug"),
    )
    with op.batch_alter_table("valuz_project_connector", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_valuz_project_connector_user_id"), ["user_id"], unique=False
        )

    # 2. sparse connector attributes (key → value). No DB-level ForeignKey
    #    (matches the other valuz_* tables); carries the owner user_id.
    op.create_table(
        "valuz_connector_attr",
        sa.Column("connector_id", sa.String(length=36), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("connector_id", "key"),
    )
    op.create_index(
        op.f("ix_valuz_connector_attr_user_id"), "valuz_connector_attr", ["user_id"]
    )

    # 3. move blob columns + FileSecretStore secrets into the attr table.
    _extract_connector_attrs()

    # 4. drop the now-extracted blob columns (incl. the consumed cred_manifest_json).
    with op.batch_alter_table("valuz_connector", schema=None) as batch_op:
        for col in (*_COLUMN_TO_KEY, "cred_manifest_json"):
            batch_op.drop_column(col)


def downgrade() -> None:
    # Re-add the blob columns and copy attr values back (unified form). The
    # consumed cred_manifest_json returns empty; the oauth_token(_expires_at)
    # attrs stay only in the FileSecretStore they were read from. Dev escape hatch.
    with op.batch_alter_table("valuz_connector", schema=None) as batch_op:
        for col in _COLUMN_TO_KEY:
            batch_op.add_column(sa.Column(col, sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("cred_manifest_json", sa.Text(), nullable=True))

    key_to_col = {key: col for col, key in _COLUMN_TO_KEY.items()}
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT connector_id, key, value FROM valuz_connector_attr")
    ).fetchall()
    for cid, key, value in rows:
        col = key_to_col.get(key)
        if col is None:
            continue
        bind.execute(
            sa.text(f"UPDATE valuz_connector SET {col} = :v WHERE id = :c"),
            {"v": value, "c": cid},
        )

    op.drop_index(
        op.f("ix_valuz_connector_attr_user_id"), table_name="valuz_connector_attr"
    )
    op.drop_table("valuz_connector_attr")

    with op.batch_alter_table("valuz_project_connector", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_valuz_project_connector_user_id"))
    op.drop_table("valuz_project_connector")
