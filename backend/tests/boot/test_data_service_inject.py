"""Tests for the owner-parametrized data-service env helper (ADR-012 PR-6)."""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src/app
from __future__ import annotations

import pytest

import valuz_agent.boot.kernel  # noqa: F401
from valuz_agent.boot.data_service_inject import data_service_env


class _FakeStore:
    """Minimal SecretStorePort double: returns a stable secret per (owner, ref)."""

    def __init__(self) -> None:
        self._d: dict[tuple[str, str], str] = {}

    def get(self, user_id: str, ref: str) -> str | None:
        return self._d.get((user_id, ref))

    def put(self, user_id: str, ref: str, value: str) -> None:
        self._d[(user_id, ref)] = value

    def delete(self, user_id: str, ref: str) -> None:
        self._d.pop((user_id, ref), None)


def test_local_store_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERNEL_STORE", "local")
    env = data_service_env(
        owner_user_id="u1", host_callback_url="http://host:8080", secret_store=_FakeStore()
    )
    assert env == {}


def test_no_callback_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERNEL_STORE", "remote")
    env = data_service_env(owner_user_id="u1", host_callback_url="", secret_store=_FakeStore())
    assert env == {}


def test_durable_injects_owner_scoped_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERNEL_STORE", "pg")
    env = data_service_env(
        owner_user_id="owner-42",
        host_callback_url="http://host:8080/",
        secret_store=_FakeStore(),
    )
    assert env["KERNEL_STORE"] == "remote"
    assert env["VALUZ_DATA_API_KIND"] == "http"
    assert env["VALUZ_DATA_API_URL"] == "http://host:8080/internal/data"  # trailing slash trimmed
    assert env["VALUZ_DATA_API_TOKEN"]  # a signed token was minted


def test_token_is_owner_scoped_and_verifiable(monkeypatch: pytest.MonkeyPatch) -> None:
    # The minted token verifies with the SAME owner's secret and carries that owner.
    monkeypatch.setenv("KERNEL_STORE", "remote")
    store = _FakeStore()
    env = data_service_env(
        owner_user_id="owner-9", host_callback_url="http://h/", secret_store=store
    )
    from valuz_agent.infra.data_service_secret import get_or_create_ds_secret
    from src.core.token_signer import HmacTokenVerifier

    secret = get_or_create_ds_secret(store, "owner-9")
    # verify() takes the raw token (data_service `_owner_dep` strips "Bearer " first).
    claims = HmacTokenVerifier(secret).verify(env["VALUZ_DATA_API_TOKEN"])
    assert claims is not None and claims.user_id == "owner-9"
