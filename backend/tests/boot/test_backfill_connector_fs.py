"""Backfill: per-project connector selection (project-config.json → DB).

Covers ``boot.backfill_connector_fs`` — importing the legacy project-config.json
``connectors`` selection into ``valuz_project_connector`` on first boot, being
DB-authoritative, and marker-gated one-time. (Connector *credentials* are
migrated by alembic 0004, not here — see tests/migrations.)
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede app.*
from __future__ import annotations

import json

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

import valuz_agent.boot.backfill_connector_fs as bf
from valuz_agent.infra.config import settings
from valuz_agent.infra.database import Base
from valuz_agent.modules.connectors.datastore import ConnectorDatastore
from valuz_agent.modules.projects.models import ProjectRow

_OWNER = "local-test-owner"


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session = async_sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    await session.close()
    await engine.dispose()


def _project_with_config(tmp_path):
    proj_root = tmp_path / "proj"
    (proj_root / ".claude").mkdir(parents=True)
    (proj_root / ".claude" / "project-config.json").write_text(
        json.dumps({"connectors": ["github", "slack"], "skills_enabled": ["x"]}),
        encoding="utf-8",
    )
    return str(proj_root)


@pytest.mark.asyncio
async def test_backfill_imports_project_selection(db, tmp_path, monkeypatch):
    proj_root = _project_with_config(tmp_path)
    db.add(ProjectRow(id="p1", name="P", kind="project", root_path=proj_root, user_id=_OWNER))
    await db.commit()
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(bf, "resolve_local_user_id", lambda: _OWNER)

    await bf.backfill_connector_fs(db)

    ds = ConnectorDatastore(db)
    # Only the ``connectors`` key is imported (skills are a separate module).
    assert sorted(await ds.get_project_connectors(_OWNER, "p1")) == ["github", "slack"]
    assert (tmp_path / _OWNER / ".connector_fs_backfilled").exists()


@pytest.mark.asyncio
async def test_backfill_is_db_authoritative_and_marker_gated(db, tmp_path, monkeypatch):
    proj_root = _project_with_config(tmp_path)
    db.add(ProjectRow(id="p1", name="P", kind="project", root_path=proj_root, user_id=_OWNER))
    await db.commit()
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(bf, "resolve_local_user_id", lambda: _OWNER)

    ds = ConnectorDatastore(db)
    # DB already carries a (different) selection — backfill must not clobber it.
    await ds.set_project_connectors(_OWNER, "p1", ["notion"])

    await bf.backfill_connector_fs(db)
    assert await ds.get_project_connectors(_OWNER, "p1") == ["notion"]

    # Marker now present → a second pass is a no-op even after we clear the DB.
    await ds.set_project_connectors(_OWNER, "p1", [])
    await bf.backfill_connector_fs(db)
    assert await ds.get_project_connectors(_OWNER, "p1") == []
