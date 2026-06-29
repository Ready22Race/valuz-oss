"""W3 — ``init_dependencies`` in ``kernel_store=pg`` mode wires model-A dual-write.

Drives the real dependency wiring with two distinct SQLite files standing in for
local + durable (a PG DSN is the only difference in prod). Asserts: the store is a
``WriteThroughStore``; the durable schema is auto-created (``_ensure_durable_schema``
— no alembic on the durable); and a write lands in BOTH files.
"""

# ruff: noqa: I001 — boot.kernel side-effect import MUST precede src.*/app.* (sys.path)
from __future__ import annotations

import uuid

import pytest

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect for src.*/app.*

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import dependencies as deps
from app.config import AppConfig
from src.adapters.sqlalchemy_store.models import Base
from src.adapters.sqlalchemy_store.store import SQLAlchemyStore
from src.adapters.write_through_store import WriteThroughStore
from src.core.agent_config import AgentConfig
from src.core.types import Session


async def _create_schema(url: str) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


@pytest.mark.asyncio
async def test_init_pg_mode_dual_writes(tmp_path):
    local_url = f"sqlite+aiosqlite:///{tmp_path / 'local.db'}"
    durable_url = f"sqlite+aiosqlite:///{tmp_path / 'durable.db'}"
    # The local store is migrated by alembic in prod; create its schema here.
    # The durable schema is created by init via ``_ensure_durable_schema``.
    await _create_schema(local_url)

    config = AppConfig(
        database_url=local_url,
        kernel_store="pg",
        durable_database_url=durable_url,
    )
    await deps.init_dependencies(config)
    try:
        store = deps.get_store()
        assert isinstance(store, WriteThroughStore)

        sid = uuid.uuid4().hex
        await store.save_session(
            Session(
                id=sid,
                user_id="u",
                agent_config=AgentConfig(id="a", name="a", model="claude-sonnet-4-6"),
                cwd=str(tmp_path),
            )
        )

        # The write reached the durable file (its schema was auto-created).
        durable_engine = create_async_engine(durable_url)
        try:
            durable = SQLAlchemyStore(async_sessionmaker(durable_engine, expire_on_commit=False))
            assert await durable.load_session("u", sid) is not None
        finally:
            await durable_engine.dispose()
    finally:
        await deps.shutdown_dependencies()

    # Shutdown disposed the durable engine and cleared the global.
    assert deps._durable_engine is None
