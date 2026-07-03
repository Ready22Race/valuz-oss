"""Migration 0016 repairs duplicate skill-index slug rows before indexing."""

from __future__ import annotations

import importlib.util
import pathlib

from sqlalchemy import create_engine, inspect, text

_MIG = (
    pathlib.Path(__file__).resolve().parents[2]
    / "alembic"
    / "host"
    / "versions"
    / "0016_skill_index_user_slug_unique.py"
)


class _Op:
    def __init__(self, conn) -> None:  # type: ignore[no-untyped-def]
        self._conn = conn

    def get_bind(self):  # type: ignore[no-untyped-def]
        return self._conn

    def create_index(
        self, name: str, table_name: str, columns: list[str], unique: bool = False
    ) -> None:
        unique_sql = "UNIQUE " if unique else ""
        cols = ", ".join(columns)
        self._conn.execute(text(f"CREATE {unique_sql}INDEX {name} ON {table_name} ({cols})"))

    def drop_index(self, name: str, table_name: str) -> None:
        self._conn.execute(text(f"DROP INDEX {name}"))


def _load():
    spec = importlib.util.spec_from_file_location("mig0016", _MIG)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _create_skill_index(conn) -> None:  # type: ignore[no-untyped-def]
    conn.execute(
        text(
            """
            CREATE TABLE valuz_skill_index (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                slug TEXT NOT NULL,
                status TEXT NOT NULL,
                scope TEXT NOT NULL,
                readonly BOOLEAN NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                library_enabled BOOLEAN NOT NULL,
                creation_origin TEXT,
                origin_json TEXT
            )
            """
        )
    )


def test_upgrade_deduplicates_skill_slugs_before_creating_unique_index() -> None:
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        _create_skill_index(conn)
        conn.execute(
            text(
                """
                INSERT INTO valuz_skill_index
                    (id, user_id, slug, status, scope, readonly, created_at,
                     updated_at, library_enabled, creation_origin, origin_json)
                VALUES
                    ('official:demo', 'u1', 'demo', 'available', 'official', 1,
                     10, 10, 0, 'imported', '{"type":"url"}'),
                    ('generatedkeep', 'u1', 'demo', 'available', 'user', 0,
                     20, 20, 1, NULL, NULL),
                    ('other', 'u2', 'demo', 'available', 'user', 0,
                     30, 30, 1, NULL, NULL)
                """
            )
        )

        migration = _load()
        migration.op = _Op(conn)
        migration.upgrade()

        rows = conn.execute(
            text(
                """
                SELECT user_id, slug, scope, library_enabled, creation_origin, origin_json
                FROM valuz_skill_index
                ORDER BY user_id
                """
            )
        ).fetchall()

    assert rows == [
        ("u1", "demo", "user", False, "imported", '{"type":"url"}'),
        ("u2", "demo", "user", True, None, None),
    ]
    indexes = inspect(engine).get_indexes("valuz_skill_index")
    assert any(
        index["name"] == "ux_valuz_skill_index_user_slug"
        and index["unique"]
        and index["column_names"] == ["user_id", "slug"]
        for index in indexes
    )


def test_upgrade_rewrites_legacy_manifest_ids() -> None:
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        _create_skill_index(conn)
        conn.execute(
            text(
                """
                INSERT INTO valuz_skill_index
                    (id, user_id, slug, status, scope, readonly, created_at,
                     updated_at, library_enabled, creation_origin, origin_json)
                VALUES
                    ('official:demo', 'u1', 'demo', 'available', 'official', 1,
                     10, 10, 1, NULL, NULL)
                """
            )
        )

        migration = _load()
        migration.op = _Op(conn)
        migration.upgrade()
        row_id = conn.execute(text("SELECT id FROM valuz_skill_index")).scalar_one()

    assert row_id != "official:demo"
    assert len(row_id) == 32
    assert all(char in "0123456789abcdef" for char in row_id)
