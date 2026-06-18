"""Resolving an installed-but-unauthorised OAuth connector must skip, not crash.

Regression for the onboarding crash: ``create_example_project`` /
``_ensure_valuz_helper`` built a ``ConnectorService`` with ``secrets=None``, so
deploying a team agent bound to an installed OAuth connector (Valuz) hit
``secrets.get(...)`` on ``None`` → ``AttributeError`` and the whole team deploy
failed ("created 0 agent(s)"). With a real secret store, a token-less OAuth
connector resolves to nothing and is simply skipped.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Side-effect import — surfaces the kernel's ``app`` / ``src.core`` on sys.path
# before the mcp resolver (loaded lazily inside resolve_mcp_servers) imports
# ``app.schemas``.
import valuz_agent.boot.kernel  # noqa: F401,E402
from valuz_agent.infra.database import Base
from valuz_agent.infra.secret_store import FileSecretStore
from valuz_agent.modules.connectors.datastore import ConnectorDatastore
from valuz_agent.modules.connectors.models import ConnectorRow
from valuz_agent.modules.connectors.service import ConnectorService

USER = "local-test-owner"  # matches the autouse owner-context fixture


@pytest.fixture
async def db(tmp_path) -> AsyncIterator:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'resolve.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[ConnectorRow.__table__])
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()


async def _install_valuz(db) -> None:
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
            status="pending_auth",
        )
    )
    await db.commit()


async def test_real_secret_store_skips_unauthorised_oauth(db, tmp_path) -> None:
    """The fixed path: a real (empty) secret store → no token → skip, no crash."""
    await _install_valuz(db)
    svc = ConnectorService(
        datastore=ConnectorDatastore(db),
        secrets=FileSecretStore(tmp_path / "secrets"),
    )

    out = await svc.resolve_mcp_servers(["valuz-search"])

    assert out == []  # skipped because there's no stored OAuth token — and no crash


async def test_none_secret_store_reproduces_the_crash(db) -> None:
    """The bug: a None secret store crashes when an OAuth connector is resolved
    (this is what onboarding's ``secrets=None`` did)."""
    await _install_valuz(db)
    svc = ConnectorService(datastore=ConnectorDatastore(db), secrets=None)  # type: ignore[arg-type]

    with pytest.raises(AttributeError):
        await svc.resolve_mcp_servers(["valuz-search"])
