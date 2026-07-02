"""Host-persistent DataService JWT secret — generate-once + stable."""

from __future__ import annotations

from valuz_agent.infra.data_service_secret import (
    DS_SECRET_REF,
    get_or_create_ds_secret,
)


class _FakeStore:
    def __init__(self) -> None:
        self._d: dict[tuple[str, str], str] = {}

    def get(self, user_id: str, ref: str) -> str | None:
        return self._d.get((user_id, ref))

    def put(self, user_id: str, ref: str, value: str) -> None:
        self._d[(user_id, ref)] = value


def _patch_secret_store(monkeypatch, store: _FakeStore) -> None:  # noqa: ANN001
    from valuz_agent.infra import secret_store

    monkeypatch.setattr(secret_store, "get", store.get)
    monkeypatch.setattr(secret_store, "put", store.put)


def test_generates_then_reuses(monkeypatch):
    store = _FakeStore()
    _patch_secret_store(monkeypatch, store)
    first = get_or_create_ds_secret("owner-1")
    assert first  # non-empty
    # Persisted under the documented ref, and stable across calls.
    assert store.get("owner-1", DS_SECRET_REF) == first
    assert get_or_create_ds_secret("owner-1") == first


def test_per_owner_isolation(monkeypatch):
    store = _FakeStore()
    _patch_secret_store(monkeypatch, store)
    a = get_or_create_ds_secret("owner-a")
    b = get_or_create_ds_secret("owner-b")
    assert a != b
