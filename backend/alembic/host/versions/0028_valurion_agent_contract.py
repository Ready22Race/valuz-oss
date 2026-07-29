"""agents: add Valurion identity, resource, and prompt-inheritance contract

Revision ID: 0028
Revises: 0027
Create Date: 2026-07-29

"""

import json
import uuid
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "valuz_agent"
_LEGACY_SLUG = "valuz-helper"
_CANONICAL_SLUG = "valurion"
_LEGACY_NAMES = {
    "Valuz Helper",
    "Valuz 小助手",
    "onboarding.valuzHelper.name",
}
_LEGACY_DESCRIPTIONS = {
    "Valuz onboarding assistant. Ask anything about using Valuz, how to plan tasks, "
    "or how to configure agents.",
    "Valuz 使用助手。教你怎么用 Valuz、帮你理清任务怎么建、Agent 怎么配，随手问。",
    "onboarding.valuzHelper.description",
}


def _json_list(value: object) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return []
    return [str(item) for item in value] if isinstance(value, list) else []


def _looks_like_unmodified_seed(row: sa.RowMapping) -> bool:
    instructions = str(row.get("instructions") or "")
    return (
        row.get("source") == "official"
        and row.get("name") in _LEGACY_NAMES
        and row.get("description") in _LEGACY_DESCRIPTIONS
        and (
            instructions == "onboarding.valuzHelper.instructions"
            or "Valuz Helper" in instructions
            or "Valuz 小助手" in instructions
        )
        and _json_list(row.get("skills")) == ["valuz-handbook"]
        and set(_json_list(row.get("connector_types")))
        <= {"valuz-search", "valuz-stock", "valuz-following"}
        and row.get("avatar") in (None, "bot")
    )


def _next_copy_slug(bind: sa.Connection, user_id: str) -> str:
    base = "valuz-helper-copy"
    taken = {
        str(item[0])
        for item in bind.execute(
            sa.text("SELECT slug FROM valuz_agent WHERE user_id = :user_id"),
            {"user_id": user_id},
        )
    }
    if base not in taken:
        return base
    suffix = 2
    while f"{base}-{suffix}" in taken:
        suffix += 1
    return f"{base}-{suffix}"


def _protect_agent_copy(bind: sa.Connection, row: sa.RowMapping) -> None:
    copy_slug = _next_copy_slug(bind, str(row["user_id"]))
    bind.execute(
        sa.text(
            """
            INSERT INTO valuz_agent (
                slug, name, description, instructions, runtime, model, skills,
                connector_types, knowledge_scope, provider_id, effort, kind,
                resource_policy, inherit_global_instructions, permission_mode,
                source, readonly, deletable, avatar, id, created_at, updated_at,
                user_id
            ) VALUES (
                :slug, :name, :description, :instructions, :runtime, :model,
                :skills, :connector_types, :knowledge_scope, :provider_id,
                :effort, 'standard', 'explicit', :inherit_global_instructions,
                :permission_mode, 'user', 0, 1, :avatar, :id, :created_at,
                :updated_at, :user_id
            )
            """
        ),
        {
            "slug": copy_slug,
            "name": f"{row['name']} Copy",
            "description": row.get("description") or "",
            "instructions": row.get("instructions") or "",
            "runtime": row.get("runtime") or "claude_agent",
            "model": row.get("model") or "",
            "skills": json.dumps(_json_list(row.get("skills"))),
            "connector_types": json.dumps(_json_list(row.get("connector_types"))),
            "knowledge_scope": json.dumps(_json_list(row.get("knowledge_scope"))),
            "provider_id": row.get("provider_id"),
            "effort": row.get("effort"),
            "inherit_global_instructions": bool(row.get("inherit_global_instructions", True)),
            "permission_mode": row.get("permission_mode") or "full_access",
            "avatar": row.get("avatar"),
            "id": str(uuid.uuid4()),
            "created_at": row.get("created_at") or 0,
            "updated_at": row.get("updated_at") or 0,
            "user_id": row["user_id"],
        },
    )


def _repair_canonical(bind: sa.Connection, user_id: str) -> None:
    bind.execute(
        sa.text(
            """
            UPDATE valuz_agent
            SET name = 'Valurion',
                description = :description,
                instructions = '',
                skills = :empty,
                connector_types = :empty,
                knowledge_scope = :empty,
                kind = 'system',
                resource_policy = 'all_available',
                inherit_global_instructions = 1,
                permission_mode = 'full_access',
                source = 'builtin',
                readonly = 1,
                deletable = 0,
                avatar = 'bot'
            WHERE user_id = :user_id AND slug = :slug
            """
        ),
        {
            "description": (
                "Your built-in assistant with access to all resources currently available to you."
            ),
            "empty": json.dumps([]),
            "user_id": user_id,
            "slug": _CANONICAL_SLUG,
        },
    )


def _migrate_channel_bindings(bind: sa.Connection) -> None:
    legacy_rows = bind.execute(
        sa.text(
            """
            SELECT * FROM valuz_agent_channel_binding
            WHERE agent_slug = :legacy
            """
        ),
        {"legacy": _LEGACY_SLUG},
    ).mappings()
    for legacy in legacy_rows:
        current = (
            bind.execute(
                sa.text(
                    """
                    SELECT * FROM valuz_agent_channel_binding
                    WHERE user_id = :user_id AND platform = :platform
                      AND agent_slug = :canonical
                    LIMIT 1
                    """
                ),
                {
                    "user_id": legacy["user_id"],
                    "platform": legacy["platform"],
                    "canonical": _CANONICAL_SLUG,
                },
            )
            .mappings()
            .first()
        )
        if current is None:
            bind.execute(
                sa.text(
                    """
                    UPDATE valuz_agent_channel_binding
                    SET agent_slug = :canonical
                    WHERE id = :id
                    """
                ),
                {"canonical": _CANONICAL_SLUG, "id": legacy["id"]},
            )
            continue
        if not current.get("secret_ref") and legacy.get("secret_ref"):
            bind.execute(
                sa.text(
                    """
                    UPDATE valuz_agent_channel_binding
                    SET secret_ref = :secret_ref
                    WHERE id = :id
                    """
                ),
                {"secret_ref": legacy["secret_ref"], "id": current["id"]},
            )
        bind.execute(
            sa.text("DELETE FROM valuz_agent_channel_binding WHERE id = :id"),
            {"id": legacy["id"]},
        )


def _migrate_mutable_references(bind: sa.Connection) -> None:
    values = {"legacy": _LEGACY_SLUG, "canonical": _CANONICAL_SLUG}
    bind.execute(
        sa.text(
            """
            DELETE FROM valuz_channel_thread_binding
            WHERE agent_slug = :legacy
              AND EXISTS (
                SELECT 1 FROM valuz_channel_thread_binding AS canonical
                WHERE canonical.user_id = valuz_channel_thread_binding.user_id
                  AND canonical.channel_instance_id =
                      valuz_channel_thread_binding.channel_instance_id
                  AND canonical.external_chat_id =
                      valuz_channel_thread_binding.external_chat_id
                  AND canonical.external_thread_id =
                      valuz_channel_thread_binding.external_thread_id
                  AND canonical.project_id =
                      valuz_channel_thread_binding.project_id
                  AND canonical.agent_slug = :canonical
              )
            """
        ),
        values,
    )
    statements = (
        (
            "UPDATE valuz_project_member SET source_agent_slug = :canonical "
            "WHERE source_agent_slug = :legacy"
        ),
        (
            "UPDATE valuz_automation SET agent_slug = :canonical "
            "WHERE agent_kind = 'library_agent' AND agent_slug = :legacy"
        ),
        (
            "UPDATE valuz_channel_chat_binding SET default_agent_slug = :canonical "
            "WHERE default_agent_slug = :legacy"
        ),
        (
            "UPDATE valuz_channel_thread_binding SET agent_slug = :canonical "
            "WHERE agent_slug = :legacy"
        ),
    )
    for statement in statements:
        bind.execute(sa.text(statement), values)
    _migrate_channel_bindings(bind)


def _migrate_valuz_helper() -> None:
    bind = op.get_bind()
    owners = {
        str(row[0])
        for row in bind.execute(
            sa.text(
                """
                SELECT DISTINCT user_id FROM valuz_agent
                WHERE slug IN (:legacy, :canonical)
                """
            ),
            {"legacy": _LEGACY_SLUG, "canonical": _CANONICAL_SLUG},
        )
    }
    for user_id in owners:
        rows = {
            str(row["slug"]): row
            for row in bind.execute(
                sa.text(
                    """
                    SELECT * FROM valuz_agent
                    WHERE user_id = :user_id
                      AND slug IN (:legacy, :canonical)
                    """
                ),
                {
                    "user_id": user_id,
                    "legacy": _LEGACY_SLUG,
                    "canonical": _CANONICAL_SLUG,
                },
            ).mappings()
        }
        legacy = rows.get(_LEGACY_SLUG)
        current = rows.get(_CANONICAL_SLUG)
        if legacy is not None and not _looks_like_unmodified_seed(legacy):
            _protect_agent_copy(bind, legacy)
        if current is not None and current.get("kind") != "system":
            _protect_agent_copy(bind, current)

        if legacy is not None:
            if current is not None:
                bind.execute(
                    sa.text("DELETE FROM valuz_agent WHERE user_id = :user_id AND slug = :slug"),
                    {"user_id": user_id, "slug": _LEGACY_SLUG},
                )
            else:
                bind.execute(
                    sa.text(
                        """
                        UPDATE valuz_agent SET slug = :canonical
                        WHERE user_id = :user_id AND slug = :legacy
                        """
                    ),
                    {
                        "canonical": _CANONICAL_SLUG,
                        "legacy": _LEGACY_SLUG,
                        "user_id": user_id,
                    },
                )
        _repair_canonical(bind, user_id)
    _migrate_mutable_references(bind)


def upgrade() -> None:
    with op.batch_alter_table(_TABLE) as batch:
        batch.add_column(
            sa.Column(
                "knowledge_scope",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )
        batch.add_column(
            sa.Column(
                "kind",
                sa.String(length=16),
                nullable=False,
                server_default="standard",
            )
        )
        batch.add_column(
            sa.Column(
                "resource_policy",
                sa.String(length=24),
                nullable=False,
                server_default="explicit",
            )
        )
        batch.add_column(
            sa.Column(
                "inherit_global_instructions",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )
        batch.add_column(
            sa.Column(
                "permission_mode",
                sa.String(length=32),
                nullable=False,
                server_default="full_access",
            )
        )
        batch.create_check_constraint(
            "ck_valuz_agent_kind",
            "kind IN ('system', 'standard')",
        )
        batch.create_check_constraint(
            "ck_valuz_agent_resource_policy",
            "resource_policy IN ('explicit', 'all_available')",
        )
    _migrate_valuz_helper()


def downgrade() -> None:
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_constraint(
            "ck_valuz_agent_resource_policy",
            type_="check",
        )
        batch.drop_constraint("ck_valuz_agent_kind", type_="check")
        batch.drop_column("permission_mode")
        batch.drop_column("inherit_global_instructions")
        batch.drop_column("resource_policy")
        batch.drop_column("kind")
        batch.drop_column("knowledge_scope")
