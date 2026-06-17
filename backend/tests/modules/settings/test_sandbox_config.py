"""Persisted sandbox-driver config (UI-driven remote sandbox).

Round-trip + secret redaction + apply_to_settings populating the settings
singleton (so boot can provision from it) + the env-wins guard.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.infra.auth_context import reset_current_user_id, set_current_user_id
from valuz_agent.infra.database import Base
from valuz_agent.modules.settings import sandbox_config as sc
from valuz_agent.modules.settings.models import AppSettingRow


@pytest.fixture
def sm(tmp_path, monkeypatch):
    from valuz_agent.infra.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    db_file = tmp_path / "s.db"
    create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(
        create_engine(f"sqlite:///{db_file}"), tables=[AppSettingRow.__table__]
    )
    token = set_current_user_id("owner-1")
    try:
        yield async_sessionmaker(
            bind=create_async_engine(f"sqlite+aiosqlite:///{db_file}"), expire_on_commit=False
        )
    finally:
        reset_current_user_id(token)


async def test_default_is_inprocess(sm) -> None:
    async with sm() as db:
        v = await sc.get_sandbox_config(db)
    assert v.driver == "inprocess"
    assert v.ags_api_key_present is False
    assert v.ags_kernel_token_present is False


async def test_set_roundtrip_secrets_redacted(sm) -> None:
    async with sm() as db:
        await sc.set_sandbox_config(
            db,
            driver="ags",
            ags_domain="ap-beijing.tencentags.com",
            ags_template="valuz-dev-tutu",
            cos_bucket="valuz-test-1252068037",
            ags_api_key="e2b_secret",
            ags_kernel_token="tok-123",
            cos_secret_id="AKID",
            cos_secret_key="SK",
        )
        v = await sc.get_sandbox_config(db)
    assert v.driver == "ags"
    assert v.ags_template == "valuz-dev-tutu"
    assert v.cos_bucket == "valuz-test-1252068037"
    # secrets never surfaced — presence only
    assert v.ags_api_key_present is True
    assert v.ags_kernel_token_present is True
    assert v.cos_secret_id_present is True
    assert v.cos_secret_key_present is True
    assert sc._secret_store().get(sc.REF_AGS_API_KEY) == "e2b_secret"  # type: ignore[attr-defined]
    assert sc._secret_store().get(sc.REF_AGS_KERNEL_TOKEN) == "tok-123"  # type: ignore[attr-defined]


async def test_bad_driver_rejected(sm) -> None:
    async with sm() as db:
        with pytest.raises(ValueError):
            await sc.set_sandbox_config(db, driver="nonsense")


async def test_apply_to_settings_populates_and_returns_ags(sm, monkeypatch) -> None:
    from valuz_agent.infra.config import settings

    monkeypatch.delenv("VALUZ_SANDBOX_DRIVER", raising=False)
    monkeypatch.setattr(settings, "ags_kernel_template", None)
    monkeypatch.setattr(settings, "cos_bucket", None)
    async with sm() as db:
        await sc.set_sandbox_config(
            db,
            driver="ags",
            ags_domain="ap-beijing.tencentags.com",
            ags_template="valuz-dev-tutu",
            cos_bucket="valuz-test-1252068037",
            ags_api_key="e2b_secret",
            ags_kernel_token="tok-xyz",
            cos_secret_id="AKID",
            cos_secret_key="SK",
        )
        driver = await sc.apply_to_settings(db)
    assert driver == "ags"
    assert settings.ags_kernel_template == "valuz-dev-tutu"
    assert settings.cos_bucket == "valuz-test-1252068037"
    assert settings.ags_api_key == "e2b_secret"
    assert settings.ags_kernel_token == "tok-xyz"
    assert settings.cos_secret_id == "AKID"


async def test_apply_noop_when_env_driver_set(sm, monkeypatch) -> None:
    monkeypatch.setenv("VALUZ_SANDBOX_DRIVER", "ags")
    async with sm() as db:
        await sc.set_sandbox_config(db, driver="ags", ags_template="t", cos_bucket="b")
        assert await sc.apply_to_settings(db) is None  # explicit env wins


async def test_apply_noop_when_incomplete(sm, monkeypatch) -> None:
    monkeypatch.delenv("VALUZ_SANDBOX_DRIVER", raising=False)
    async with sm() as db:
        await sc.set_sandbox_config(db, driver="ags")  # no api_key/template/bucket
        assert await sc.apply_to_settings(db) is None
