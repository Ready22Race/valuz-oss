"""Tests for the owner-parametrized data-service env helper (ADR-012 PR-6)."""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src/app
from __future__ import annotations

import pytest

import valuz_agent.boot.kernel  # noqa: F401
from valuz_agent.boot.data_service_inject import data_service_env


class _FakeStore:
    """Minimal secret store double: returns a stable secret per (owner, ref)."""

    def __init__(self) -> None:
        self._d: dict[tuple[str, str], str] = {}

    def get(self, user_id: str, ref: str) -> str | None:
        return self._d.get((user_id, ref))

    def put(self, user_id: str, ref: str, value: str) -> None:
        self._d[(user_id, ref)] = value

    def delete(self, user_id: str, ref: str) -> None:
        self._d.pop((user_id, ref), None)


def _patch_secret_store(monkeypatch: pytest.MonkeyPatch, store: _FakeStore) -> None:
    from valuz_agent.infra import secret_store

    monkeypatch.setattr(secret_store, "get", store.get)
    monkeypatch.setattr(secret_store, "put", store.put)
    monkeypatch.setattr(secret_store, "delete", store.delete)


def test_local_store_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERNEL_STORE", "local")
    env = data_service_env(owner_user_id="u1", host_callback_url="http://host:8080")
    assert env == {}


def test_no_callback_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERNEL_STORE", "remote")
    env = data_service_env(owner_user_id="u1", host_callback_url="")
    assert env == {}


def test_durable_injects_owner_scoped_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERNEL_STORE", "pg")
    _patch_secret_store(monkeypatch, _FakeStore())
    env = data_service_env(
        owner_user_id="owner-42",
        host_callback_url="http://host:8080/",
    )
    assert env["KERNEL_STORE"] == "remote"
    assert env["VALUZ_DATA_API_KIND"] == "http"
    # ADR-013: minted URLs use the new "/_internal/..." path; the legacy
    # "/internal/..." mount stays reachable (see api/app.py::_mount_internal)
    # but is never generated for new sandboxes.
    assert env["VALUZ_DATA_API_URL"] == "http://host:8080/_internal/data"  # trailing slash trimmed
    assert env["VALUZ_DATA_API_TOKEN"]  # a signed token was minted


def test_token_is_owner_scoped_and_verifiable(monkeypatch: pytest.MonkeyPatch) -> None:
    # The minted token verifies with the SAME owner's secret and carries that owner.
    monkeypatch.setenv("KERNEL_STORE", "remote")
    store = _FakeStore()
    _patch_secret_store(monkeypatch, store)
    env = data_service_env(owner_user_id="owner-9", host_callback_url="http://h/")
    from valuz_agent.infra.data_service_secret import get_or_create_ds_secret
    from src.core.token_signer import HmacTokenVerifier

    secret = get_or_create_ds_secret("owner-9")
    # verify() takes the raw token (data_service `_owner_dep` strips "Bearer " first).
    claims = HmacTokenVerifier(secret).verify(env["VALUZ_DATA_API_TOKEN"])
    assert claims is not None and claims.user_id == "owner-9"


# ── per-owner verifier (PR-6 S2: multi-tenant data-service auth) ──────────


def test_per_owner_verifies_the_right_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    from valuz_agent.boot.kernel import (
        make_host_data_service_verifier_per_owner,
        mint_data_service_token,
    )
    from valuz_agent.infra.data_service_secret import get_or_create_ds_secret

    store = _FakeStore()
    _patch_secret_store(monkeypatch, store)
    tok_a = mint_data_service_token(get_or_create_ds_secret("A"), user_id="A")
    tok_b = mint_data_service_token(get_or_create_ds_secret("B"), user_id="B")
    v = make_host_data_service_verifier_per_owner()
    assert v.verify(tok_a).user_id == "A"  # each owner's token resolves its own secret
    assert v.verify(tok_b).user_id == "B"


def test_per_owner_rejects_unknown_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.core.token_signer import InvalidTokenError

    from valuz_agent.boot.kernel import (
        make_host_data_service_verifier_per_owner,
        mint_data_service_token,
    )

    tok = mint_data_service_token("some-secret", user_id="ghost")  # ghost has no stored secret
    _patch_secret_store(monkeypatch, _FakeStore())
    v = make_host_data_service_verifier_per_owner()
    with pytest.raises(InvalidTokenError):
        v.verify(tok)


def test_per_owner_rejects_forged_sub(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.core.token_signer import InvalidTokenError

    from valuz_agent.boot.kernel import (
        make_host_data_service_verifier_per_owner,
        mint_data_service_token,
    )
    from valuz_agent.infra.data_service_secret import get_or_create_ds_secret

    store = _FakeStore()
    _patch_secret_store(monkeypatch, store)
    get_or_create_ds_secret("victim")  # victim has a real secret
    forged = mint_data_service_token("attacker-secret", user_id="victim")  # signed with wrong key
    v = make_host_data_service_verifier_per_owner()
    with pytest.raises(InvalidTokenError):  # sub picks victim's real secret → bad signature
        v.verify(forged)


def test_per_owner_none_token_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    from valuz_agent.boot.kernel import make_host_data_service_verifier_per_owner

    _patch_secret_store(monkeypatch, _FakeStore())
    assert make_host_data_service_verifier_per_owner().verify(None) is None
