"""Migration 0022 swaps the skill-index unique key to (user_id, source_path).

This lets a bundled ``official`` copy and a ``user`` copy of the same slug
coexist as two rows; downgrade collapses same-slug rows (keeping the official
copy) before restoring the old ``(user_id, slug)`` unique index.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

_MIG = (
    pathlib.Path(__file__).resolve().parents[2]
    / "alembic"
    / "host"
    / "versions"
    / "0022_skill_index_source_path_unique.py"
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
    spec = importlib.util.spec_from_file_location("mig0022", _MIG)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _create_skill_index(conn, *, with_old_index: bool) -> None:  # type: ignore[no-untyped-def]
    conn.execute(
        text(
            """
            CREATE TABLE valuz_skill_index (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                slug TEXT NOT NULL,
                name TEXT NOT NULL,
                scope TEXT NOT NULL,
                source_path TEXT NOT NULL,
                status TEXT NOT NULL,
                library_enabled BOOLEAN NOT NULL
            )
            """
        )
    )
    if with_old_index:
        conn.execute(
            text(
                "CREATE UNIQUE INDEX ux_valuz_skill_index_user_slug "
                "ON valuz_skill_index (user_id, slug)"
            )
        )


def _insert(conn, **cols) -> None:  # type: ignore[no-untyped-def]
    keys = ", ".join(cols)
    binds = ", ".join(f":{k}" for k in cols)
    conn.execute(text(f"INSERT INTO valuz_skill_index ({keys}) VALUES ({binds})"), cols)


def test_upgrade_swaps_to_source_path_unique_and_allows_same_slug() -> None:
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        _create_skill_index(conn, with_old_index=True)
        _insert(
            conn, id="r1", user_id="u", slug="shared", name="shared", scope="official",
            source_path="/official/shared", status="available", library_enabled=1,
        )

        migration = _load()
        migration.op = _Op(conn)
        migration.upgrade()

        # A same-slug copy in a different dir is now legal.
        _insert(
            conn, id="r2", user_id="u", slug="shared", name="shared", scope="user",
            source_path="/home/.agents/skills/shared", status="available", library_enabled=0,
        )
        # ...but the same (user_id, source_path) still collides.
        with pytest.raises(IntegrityError):
            _insert(
                conn, id="r3", user_id="u", slug="other", name="other", scope="user",
                source_path="/official/shared", status="available", library_enabled=0,
            )

    indexes = inspect(engine).get_indexes("valuz_skill_index")
    assert any(
        idx["name"] == "ux_valuz_skill_index_user_source_path"
        and idx["unique"]
        and idx["column_names"] == ["user_id", "source_path"]
        for idx in indexes
    )
    assert not any(idx["name"] == "ux_valuz_skill_index_user_slug" for idx in indexes)


def test_downgrade_collapses_same_slug_keeping_official() -> None:
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        _create_skill_index(conn, with_old_index=False)
        conn.execute(
            text(
                "CREATE UNIQUE INDEX ux_valuz_skill_index_user_source_path "
                "ON valuz_skill_index (user_id, source_path)"
            )
        )
        _insert(
            conn, id="r1", user_id="u", slug="shared", name="shared", scope="user",
            source_path="/home/.agents/skills/shared", status="available", library_enabled=0,
        )
        _insert(
            conn, id="r2", user_id="u", slug="shared", name="shared", scope="official",
            source_path="/official/shared", status="available", library_enabled=1,
        )

        migration = _load()
        migration.op = _Op(conn)
        migration.downgrade()

        rows = conn.execute(
            text("SELECT scope FROM valuz_skill_index WHERE slug='shared'")
        ).fetchall()

    # Collapsed to a single row, keeping the official copy.
    assert [r[0] for r in rows] == ["official"]
    indexes = inspect(engine).get_indexes("valuz_skill_index")
    assert any(
        idx["name"] == "ux_valuz_skill_index_user_slug" and idx["unique"] for idx in indexes
    )
    assert not any(
        idx["name"] == "ux_valuz_skill_index_user_source_path" for idx in indexes
    )
