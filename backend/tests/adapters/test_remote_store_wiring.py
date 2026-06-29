"""Phase A — kernel dependency wiring for the remote store seam.

Covers the ``init_dependencies`` durable write-through helper
(``_build_durable_store``) and the token-aware ``get_owner_id`` branch.
No DB, no network.
"""

# ruff: noqa: I001 — boot.kernel side-effect import MUST precede src.*/app.* (sys.path)
from __future__ import annotations

import pytest

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect for src.*/app.*

from fastapi import HTTPException

from app import dependencies as deps
from app.config import AppConfig
from src.adapters.remote_store import register_remote_backend
from src.core.token_verifier import NullTokenVerifier, OwnerClaims


@pytest.fixture
def restore_verifier():
    """Restore the default NullTokenVerifier after a test rebinds it."""
    yield
    deps.set_token_verifier(NullTokenVerifier())


def test_default_kernel_store_is_local():
    # local-first default — zero behaviour change unless explicitly opted in.
    assert AppConfig(kernel_store="local").kernel_store == "local"


def test_local_store_has_no_durable():
    # Model A default: local-only, no durable write-through target.
    assert deps._build_durable_store(AppConfig(kernel_store="local")) is None


def test_build_durable_store_requires_url():
    config = AppConfig(kernel_store="remote", data_api_url=None)
    with pytest.raises(RuntimeError, match="VALUZ_DATA_API_URL"):
        deps._build_durable_store(config)


async def test_build_durable_store_wires_kind_url_and_token():
    captured: dict = {}

    def _factory(**kw):
        captured.update(kw)
        return "STORE-SENTINEL"

    register_remote_backend("wire-test", _factory)
    config = AppConfig(
        kernel_store="remote",
        data_api_url="http://127.0.0.1:3000",
        data_api_token="jwt-tok",
        data_api_kind="wire-test",
    )
    result = deps._build_durable_store(config)

    assert result == "STORE-SENTINEL"
    assert captured["base_url"] == "http://127.0.0.1:3000"
    # The access-token hook returns the configured bearer (static for now).
    assert await captured["access_token"]() == "jwt-tok"


def test_get_owner_id_header_path():
    # OSS default (NullTokenVerifier): owner comes from the trusted header.
    assert deps.get_owner_id(x_valuz_owner_id="u1", authorization=None) == "u1"


def test_get_owner_id_missing_owner_is_403():
    with pytest.raises(HTTPException) as exc:
        deps.get_owner_id(x_valuz_owner_id=None, authorization=None)
    assert exc.value.status_code == 403


def test_get_owner_id_null_verifier_ignores_bearer():
    # A bearer token without a bound verifier must NOT become an owner.
    with pytest.raises(HTTPException) as exc:
        deps.get_owner_id(x_valuz_owner_id=None, authorization="Bearer abc.def.ghi")
    assert exc.value.status_code == 403


def test_get_owner_id_verified_token_overrides_forged_header(restore_verifier):
    class _Verifier:
        def verify(self, token):
            return OwnerClaims(user_id="real-user") if token else None

    deps.set_token_verifier(_Verifier())
    # Even with a forged X-Valuz-Owner-Id, the VERIFIED token wins.
    owner = deps.get_owner_id(x_valuz_owner_id="forged", authorization="Bearer good.token")
    assert owner == "real-user"
