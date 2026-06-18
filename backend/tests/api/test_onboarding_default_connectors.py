"""Onboarding installs the default assistant's connectors into「已添加」.

Binding a connector slug to the agent doesn't create a connector row, so the
helper must install its defaults from the catalog. These tests pin the install
logic — skip already-installed slugs, install the rest from the catalog config —
without hitting the network (``create_connector`` is mocked, so no probe/OAuth).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.api.routes.onboarding import (
    _VALUZ_HELPER_CONNECTORS,
    _ensure_default_connectors,
)
from valuz_agent.infra.database import Base
from valuz_agent.modules.connectors.models import ConnectorRow

USER = "local-test-owner"


@pytest.fixture
async def db(tmp_path) -> AsyncIterator:
    db_file = tmp_path / "default_connectors.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[ConnectorRow.__table__])
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()


def _installed_slugs(mock: AsyncMock) -> set[str]:
    return {call.kwargs["body"].slug for call in mock.call_args_list}


async def test_installs_missing_defaults_from_catalog(db) -> None:
    """All three defaults are installed via create_connector when none exist,
    with the catalog's transport/auth/url carried through."""
    created = AsyncMock()
    with patch("valuz_agent.api.routes.connectors.create_connector", created):
        await _ensure_default_connectors(USER, db)

    assert _installed_slugs(created) == set(_VALUZ_HELPER_CONNECTORS)

    firecrawl = next(
        c.kwargs["body"] for c in created.call_args_list if c.kwargs["body"].slug == "firecrawl"
    )
    assert firecrawl.transport == "http"
    assert firecrawl.auth_type == "none"
    assert firecrawl.url == "https://mcp.firecrawl.dev/v2/mcp"
    assert firecrawl.connector_type == "recommended"


async def test_skips_already_installed(db) -> None:
    """A connector that already has a row is not reinstalled."""
    db.add(
        ConnectorRow(
            id="conn-valuz-search",
            user_id=USER,
            slug="valuz-search",
            display_name="Valuz · Search",
            connector_type="recommended",
            transport="http",
            auth_type="oauth",
            url="https://mcp.reportify.cn/search/mcp",
            enabled=True,
            status="connected",
        )
    )
    await db.commit()

    created = AsyncMock()
    with patch("valuz_agent.api.routes.connectors.create_connector", created):
        await _ensure_default_connectors(USER, db)

    assert _installed_slugs(created) == {"valuz-stock", "firecrawl"}


async def test_one_failure_does_not_sink_the_rest(db) -> None:
    """A create_connector that raises for one slug is swallowed; the others
    still install (best-effort, per-slug try/except)."""

    async def flaky(*, body, svc, user_id):  # noqa: ANN001, ARG001
        if body.slug == "valuz-stock":
            raise RuntimeError("boom")

    created = AsyncMock(side_effect=flaky)
    with patch("valuz_agent.api.routes.connectors.create_connector", created):
        await _ensure_default_connectors(USER, db)

    # All three were attempted even though one raised.
    assert _installed_slugs(created) == set(_VALUZ_HELPER_CONNECTORS)
