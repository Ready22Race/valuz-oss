"""Regression: project sessions resolve their auto-default connector slugs.

``SessionService._auto_default_mcp_slugs`` reads a project's enabled connector
slugs via ``ConnectorDatastore.get_project_connectors``. The selection now lives
in the host DB (``valuz_project_connector``) rather than
``<project>/.claude/project-config.json``, so a shared multi-client backend —
which has no per-user local filesystem — resolves them the same way. This guards
that project sessions actually surface their configured connectors (chat
sessions use the other ``list_enabled`` branch).
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede app.*
from __future__ import annotations

from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from valuz_agent.infra.database import Base
from valuz_agent.modules.connectors.datastore import ConnectorDatastore
from valuz_agent.modules.sessions.service import SessionService


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    db = async_sessionmaker(bind=engine, expire_on_commit=False)()
    yield db
    await db.close()
    await engine.dispose()


class _FakeProjects:
    def __init__(self, project_row) -> None:
        self._row = project_row

    async def get_by_id(self, user_id: str, project_id: str):
        return self._row


def _service(connectors, projects) -> SessionService:
    # Bypass the heavy ctor — _auto_default_mcp_slugs only touches
    # ``_connectors`` and ``_projects``.
    svc = SessionService.__new__(SessionService)
    svc._connectors = connectors  # type: ignore[attr-defined]
    svc._projects = projects  # type: ignore[attr-defined]
    return svc


@pytest.mark.asyncio
async def test_project_session_resolves_config_connectors(session) -> None:
    # A project whose DB-backed selection declares connectors. The owner is the
    # autouse "local-test-owner" from tests/conftest.py.
    connectors = ConnectorDatastore(session)
    await connectors.set_project_connectors("local-test-owner", "p1", ["github", "slack"])

    project_row = SimpleNamespace(kind="project", root_path="/anywhere")
    svc = _service(connectors, _FakeProjects(project_row))

    slugs = await svc._auto_default_mcp_slugs("p1", user_id="local-test-owner")

    assert sorted(slugs) == ["github", "slack"]


@pytest.mark.asyncio
async def test_project_without_config_returns_empty(session) -> None:
    project_row = SimpleNamespace(kind="project", root_path="/anywhere")
    connectors = ConnectorDatastore(session)
    svc = _service(connectors, _FakeProjects(project_row))
    assert await svc._auto_default_mcp_slugs("p1", user_id="local-test-owner") == []
