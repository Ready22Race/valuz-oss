"""Data-service (durable store tier) preference helpers — persist + validate.

These back the hidden Settings → Data Service panel; ``boot.kernel`` reads them
to drive the in-process kernel's store tier. Owner-scoped via the auth context,
like every other preference helper.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.infra.auth_context import reset_current_user_id, set_current_user_id
from valuz_agent.infra.database import Base
from valuz_agent.modules.settings.models import AppSettingRow
from valuz_agent.modules.settings.preferences import (
    get_data_api_kind,
    get_data_api_token,
    get_data_api_url,
    get_durable_database_url,
    get_kernel_store,
    set_data_api_token,
    set_data_api_url,
    set_durable_database_url,
    set_kernel_store,
)


@pytest.fixture
def sm(tmp_path):
    db_file = tmp_path / "ds.db"
    sync_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(sync_engine, tables=[AppSettingRow.__table__])
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    return async_sessionmaker(bind=async_engine, expire_on_commit=False)


@pytest.fixture
def owner():
    token = set_current_user_id("user-A")
    yield "user-A"
    reset_current_user_id(token)


async def test_defaults_are_local_and_http(sm, owner):
    async with sm() as db:
        assert await get_kernel_store(db) == "local"
        assert await get_durable_database_url(db) == ""
        assert await get_data_api_url(db) == ""
        assert await get_data_api_kind(db) == "http"
        assert await get_data_api_token(db) == ""


async def test_pg_tier_round_trips(sm, owner):
    async with sm() as db:
        await set_kernel_store(db, "pg")
        await set_durable_database_url(db, "postgresql+asyncpg://u:p@h:5432/db")
    async with sm() as db:
        assert await get_kernel_store(db) == "pg"
        assert await get_durable_database_url(db) == "postgresql+asyncpg://u:p@h:5432/db"


async def test_remote_tier_round_trips(sm, owner):
    async with sm() as db:
        await set_kernel_store(db, "remote")
        await set_data_api_url(db, "http://127.0.0.1:8400")
        await set_data_api_token(db, "jwt-tok")
    async with sm() as db:
        assert await get_kernel_store(db) == "remote"
        assert await get_data_api_url(db) == "http://127.0.0.1:8400"
        assert await get_data_api_token(db) == "jwt-tok"


async def test_invalid_store_rejected(sm, owner):
    async with sm() as db:
        with pytest.raises(ValueError, match="kernel_store must be one of"):
            await set_kernel_store(db, "mysql")


async def test_owner_scoped(sm):
    token = set_current_user_id("user-A")
    try:
        async with sm() as db:
            await set_kernel_store(db, "pg")
    finally:
        reset_current_user_id(token)
    token = set_current_user_id("user-B")
    try:
        async with sm() as db:
            assert await get_kernel_store(db) == "local"  # B sees its own default
    finally:
        reset_current_user_id(token)
