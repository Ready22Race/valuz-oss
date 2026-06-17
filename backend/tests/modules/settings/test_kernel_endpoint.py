"""Persisted kernel-endpoint config ("configure sandbox address").

Round-trip + token redaction + the boot-apply precedence rule (an explicit
env / provisioned http mode wins over persisted config).
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.infra.auth_context import reset_current_user_id, set_current_user_id
from valuz_agent.infra.database import Base
from valuz_agent.modules.settings import kernel_endpoint as ke
from valuz_agent.modules.settings.models import AppSettingRow


@pytest.fixture
def sm(tmp_path, monkeypatch):
    # Isolate the secret store under tmp (the helper builds FileSecretStore
    # from settings.secrets_dir == data_dir/secrets).
    from valuz_agent.infra.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    db_file = tmp_path / "settings.db"
    sync_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(sync_engine, tables=[AppSettingRow.__table__])
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    token = set_current_user_id("owner-1")
    try:
        yield async_sessionmaker(bind=async_engine, expire_on_commit=False)
    finally:
        reset_current_user_id(token)


async def test_default_is_inprocess(sm) -> None:
    async with sm() as db:
        view = await ke.get_endpoint(db)
    assert view.mode == "inprocess"
    assert view.token_present is False


async def test_set_http_roundtrip_and_token_redacted(sm) -> None:
    async with sm() as db:
        await ke.set_endpoint(
            db,
            mode="http",
            url="https://kernel.ags.example:8000",
            host_external_url="https://my-host.lan:8000",
            token="super-secret",
        )
        view = await ke.get_endpoint(db)
    assert view.mode == "http"
    assert view.url == "https://kernel.ags.example:8000"
    assert view.host_external_url == "https://my-host.lan:8000"
    # The token is stored but never surfaced — only its presence.
    assert view.token_present is True
    assert ke._secret_store().get(ke.TOKEN_SECRET_REF) == "super-secret"  # type: ignore[attr-defined]


async def test_http_requires_url(sm) -> None:
    async with sm() as db:
        with pytest.raises(ValueError):
            await ke.set_endpoint(db, mode="http", url=None)


async def test_url_must_be_http(sm) -> None:
    async with sm() as db:
        with pytest.raises(ValueError):
            await ke.set_endpoint(db, mode="http", url="ftp://nope")


async def test_clear_token(sm) -> None:
    async with sm() as db:
        await ke.set_endpoint(db, mode="http", url="https://k:8000", token="t")
        assert (await ke.get_endpoint(db)).token_present is True
        await ke.set_endpoint(db, mode="http", url="https://k:8000", clear_token=True)
        assert (await ke.get_endpoint(db)).token_present is False


async def test_apply_persisted_sets_http(sm, monkeypatch) -> None:
    from valuz_agent.infra.config import settings

    monkeypatch.setattr(settings, "kernel_mode", "inprocess")
    monkeypatch.setattr(settings, "host_external_url", None)
    async with sm() as db:
        await ke.set_endpoint(
            db,
            mode="http",
            url="https://kernel:8000",
            host_external_url="https://host:8000",
            token="tok",
        )
        applied = await ke.apply_persisted_endpoint(db)
    assert applied is True
    assert settings.kernel_mode == "http"
    assert settings.kernel_url == "https://kernel:8000"
    assert settings.kernel_token == "tok"
    assert settings.host_external_url == "https://host:8000"


async def test_apply_persisted_noop_when_already_http(sm, monkeypatch) -> None:
    # Explicit env / provisioned sandbox wins — never clobber it.
    from valuz_agent.infra.config import settings

    monkeypatch.setattr(settings, "kernel_mode", "http")
    monkeypatch.setattr(settings, "kernel_url", "http://provisioned:1234")
    async with sm() as db:
        await ke.set_endpoint(db, mode="http", url="https://persisted:8000", token="t")
        applied = await ke.apply_persisted_endpoint(db)
    assert applied is False
    assert settings.kernel_url == "http://provisioned:1234"


async def test_apply_persisted_noop_when_inprocess(sm, monkeypatch) -> None:
    from valuz_agent.infra.config import settings

    monkeypatch.setattr(settings, "kernel_mode", "inprocess")
    async with sm() as db:
        applied = await ke.apply_persisted_endpoint(db)
    assert applied is False
    assert settings.kernel_mode == "inprocess"
