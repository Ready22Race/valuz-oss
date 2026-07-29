"""Migration 0028 protects customized Helper data and rewrites live refs."""

from __future__ import annotations

import importlib.util
import json
import pathlib

from sqlalchemy import create_engine, text

_MIG = (
    pathlib.Path(__file__).resolve().parents[2]
    / "alembic"
    / "host"
    / "versions"
    / "0028_valurion_agent_contract.py"
)


class _Op:
    def __init__(self, conn) -> None:  # type: ignore[no-untyped-def]
        self._conn = conn

    def get_bind(self):  # type: ignore[no-untyped-def]
        return self._conn


def _load():
    spec = importlib.util.spec_from_file_location("mig0028", _MIG)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _create_schema(conn) -> None:  # type: ignore[no-untyped-def]
    conn.execute(
        text(
            """
            CREATE TABLE valuz_agent (
                slug TEXT NOT NULL, name TEXT NOT NULL, description TEXT NOT NULL,
                instructions TEXT NOT NULL, runtime TEXT NOT NULL, model TEXT NOT NULL,
                skills JSON NOT NULL, connector_types JSON NOT NULL,
                knowledge_scope JSON NOT NULL, provider_id TEXT, effort TEXT,
                kind TEXT NOT NULL, resource_policy TEXT NOT NULL,
                inherit_global_instructions BOOLEAN NOT NULL,
                permission_mode TEXT NOT NULL, source TEXT NOT NULL,
                readonly BOOLEAN NOT NULL, deletable BOOLEAN NOT NULL, avatar TEXT,
                id TEXT PRIMARY KEY, created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL, user_id TEXT NOT NULL,
                UNIQUE(user_id, slug)
            )
            """
        )
    )
    conn.execute(
        text("CREATE TABLE valuz_project_member (id TEXT PRIMARY KEY, source_agent_slug TEXT)")
    )
    conn.execute(
        text(
            "CREATE TABLE valuz_automation (id TEXT PRIMARY KEY, agent_kind TEXT, agent_slug TEXT)"
        )
    )
    conn.execute(
        text(
            "CREATE TABLE valuz_channel_chat_binding (id TEXT PRIMARY KEY, default_agent_slug TEXT)"
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE valuz_channel_thread_binding (
                id TEXT PRIMARY KEY, user_id TEXT, channel_instance_id TEXT,
                external_chat_id TEXT, external_thread_id TEXT, agent_slug TEXT,
                project_id TEXT,
                UNIQUE(user_id, channel_instance_id, external_chat_id,
                       external_thread_id, agent_slug, project_id)
            )
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE valuz_agent_channel_binding (
                id TEXT PRIMARY KEY, user_id TEXT, platform TEXT,
                channel_instance_id TEXT, agent_slug TEXT, bot_id TEXT,
                secret_ref TEXT, enabled BOOLEAN, bot_name TEXT, ws_url TEXT,
                UNIQUE(user_id, platform, agent_slug)
            )
            """
        )
    )


def _insert_agent(
    conn,  # type: ignore[no-untyped-def]
    *,
    slug: str,
    instructions: str,
    source: str = "official",
    name: str = "Valuz Helper",
) -> None:
    conn.execute(
        text(
            """
            INSERT INTO valuz_agent VALUES (
                :slug, :name,
                :description,
                :instructions, 'claude_agent', 'model-1',
                '["valuz-handbook"]', '["valuz-search","valuz-stock"]', '[]',
                'valuz-channel', 'high', 'standard', 'explicit', 1,
                'full_access', :source, 0, 1, 'bot', :id, 1, 2, 'owner-1'
            )
            """
        ),
        {
            "slug": slug,
            "name": name,
            "description": (
                "Valuz onboarding assistant. Ask anything about using Valuz, "
                "how to plan tasks, or how to configure agents."
            ),
            "instructions": instructions,
            "source": source,
            "id": f"id-{slug}",
        },
    )


def test_unmodified_helper_migrates_in_place_and_rewrites_live_refs() -> None:
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        _create_schema(conn)
        _insert_agent(conn, slug="valuz-helper", instructions="You are the Valuz Helper.")
        conn.execute(text("INSERT INTO valuz_project_member VALUES ('member-1', 'valuz-helper')"))
        conn.execute(
            text(
                "INSERT INTO valuz_automation VALUES "
                "('automation-1', 'library_agent', 'valuz-helper')"
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO valuz_agent_channel_binding VALUES (
                    'binding-1', 'owner-1', 'feishu', 'bot-1',
                    'valuz-helper', 'bot-id', 'channel/feishu/valuz-helper',
                    1, NULL, NULL
                )
                """
            )
        )

        migration = _load()
        migration.op = _Op(conn)
        migration._migrate_valuz_helper()

        rows = (
            conn.execute(
                text(
                    "SELECT id, slug, kind, resource_policy, instructions, skills, "
                    "source, readonly, deletable FROM valuz_agent"
                )
            )
            .mappings()
            .all()
        )
        source_ref = conn.execute(
            text("SELECT source_agent_slug FROM valuz_project_member")
        ).scalar_one()
        automation_ref = conn.execute(text("SELECT agent_slug FROM valuz_automation")).scalar_one()
        binding = conn.execute(
            text("SELECT agent_slug, secret_ref FROM valuz_agent_channel_binding")
        ).one()

    assert len(rows) == 1
    assert rows[0]["id"] == "id-valuz-helper"
    assert rows[0]["slug"] == "valurion"
    assert rows[0]["kind"] == "system"
    assert rows[0]["resource_policy"] == "all_available"
    assert rows[0]["instructions"] == ""
    assert json.loads(rows[0]["skills"]) == []
    assert rows[0]["source"] == "builtin"
    assert bool(rows[0]["readonly"]) is True
    assert bool(rows[0]["deletable"]) is False
    assert source_ref == "valurion"
    assert automation_ref == "valurion"
    assert binding == ("valurion", "channel/feishu/valuz-helper")


def test_customized_helper_is_deep_copied_before_canonical_repair() -> None:
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        _create_schema(conn)
        _insert_agent(
            conn,
            slug="valuz-helper",
            instructions="My carefully customized workflow.",
            name="My Helper",
        )

        migration = _load()
        migration.op = _Op(conn)
        migration._migrate_valuz_helper()

        rows = (
            conn.execute(
                text(
                    "SELECT slug, name, instructions, runtime, model, provider_id, "
                    "effort, skills, connector_types, kind, resource_policy, source "
                    "FROM valuz_agent ORDER BY slug"
                )
            )
            .mappings()
            .all()
        )

    assert [row["slug"] for row in rows] == ["valurion", "valuz-helper-copy"]
    canonical, copy = rows
    assert canonical["kind"] == "system"
    assert canonical["instructions"] == ""
    assert copy["name"] == "My Helper Copy"
    assert copy["instructions"] == "My carefully customized workflow."
    assert copy["runtime"] == "claude_agent"
    assert copy["model"] == "model-1"
    assert copy["provider_id"] == "valuz-channel"
    assert copy["effort"] == "high"
    assert json.loads(copy["skills"]) == ["valuz-handbook"]
    assert json.loads(copy["connector_types"]) == ["valuz-search", "valuz-stock"]
    assert copy["kind"] == "standard"
    assert copy["resource_policy"] == "explicit"
    assert copy["source"] == "user"


def test_existing_valurion_wins_when_legacy_helper_also_exists() -> None:
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        _create_schema(conn)
        _insert_agent(
            conn,
            slug="valurion",
            instructions="Existing Valurion instructions.",
            source="builtin",
            name="Valurion",
        )
        conn.execute(
            text(
                """
                UPDATE valuz_agent
                SET runtime = 'codex', model = 'gpt-existing',
                    provider_id = 'provider-existing', effort = 'xhigh',
                    kind = 'system'
                WHERE slug = 'valurion'
                """
            )
        )
        _insert_agent(
            conn,
            slug="valuz-helper",
            instructions="My customized legacy workflow.",
            name="My Legacy Helper",
        )

        migration = _load()
        migration.op = _Op(conn)
        migration._migrate_valuz_helper()

        rows = (
            conn.execute(
                text(
                    "SELECT id, slug, instructions, runtime, model, provider_id, "
                    "effort, kind FROM valuz_agent ORDER BY slug"
                )
            )
            .mappings()
            .all()
        )

    assert [row["slug"] for row in rows] == ["valurion", "valuz-helper-copy"]
    canonical, copy = rows
    assert canonical["id"] == "id-valurion"
    assert canonical["instructions"] == ""
    assert canonical["runtime"] == "codex"
    assert canonical["model"] == "gpt-existing"
    assert canonical["provider_id"] == "provider-existing"
    assert canonical["effort"] == "xhigh"
    assert canonical["kind"] == "system"
    assert copy["instructions"] == "My customized legacy workflow."
    assert copy["kind"] == "standard"
