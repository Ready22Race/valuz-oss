"""Host-persistent DataService JWT secret — generate-once + stable."""

from __future__ import annotations

from valuz_agent.infra.data_service_secret import (
    DS_SECRET_REF,
    get_or_create_ds_secret,
)
from valuz_agent.infra.secret_store import InMemorySecretStore


def test_generates_then_reuses():
    store = InMemorySecretStore()
    first = get_or_create_ds_secret(store, "owner-1")
    assert first  # non-empty
    # Persisted under the documented ref, and stable across calls.
    assert store.get("owner-1", DS_SECRET_REF) == first
    assert get_or_create_ds_secret(store, "owner-1") == first


def test_per_owner_isolation():
    store = InMemorySecretStore()
    a = get_or_create_ds_secret(store, "owner-a")
    b = get_or_create_ds_secret(store, "owner-b")
    assert a != b
