"""Tests for cross-owner reads via the DataReader seam (fleet PR-5 S1b).

Host-wide `list_all_sessions` now goes through `data_reader()`: the durable
`LocalDataServiceReader` serves it with `user_id=None` (kernel-independent),
while the default `_KernelClientReader` delegates to the kernel client
(behavior unchanged when no host reader is bound).
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src/app
from __future__ import annotations

import valuz_agent.boot.kernel  # noqa: F401  (sys.path bootstrap)
from valuz_agent.adapters import data_reader as dr
from valuz_agent.adapters.data_service_local import LocalDataServiceReader


class _FakeStore:
    def __init__(self) -> None:
        self.list_calls: list[object] = []

    async def list_sessions(self, user_id, *, status=None, ids=None, limit=50, offset=0):
        self.list_calls.append(user_id)
        return []  # empty → skips the session_to_data serializer


async def test_local_reader_list_all_uses_none_owner() -> None:
    store = _FakeStore()
    out = await LocalDataServiceReader(store).list_all_sessions(limit=7)
    assert out == []
    assert store.list_calls == [None]  # cross-owner sweep, not a per-user read


async def test_kernel_client_reader_delegates(monkeypatch) -> None:
    from valuz_agent.adapters import kernel_client as kc

    seen: dict[str, object] = {}

    async def _fake(*, status=None, ids=None, limit=50, offset=0):
        seen.update(ids=ids, limit=limit)
        return ["S"]

    monkeypatch.setattr(kc, "list_all_sessions", _fake)
    out = await dr._KernelClientReader().list_all_sessions(ids=["x"], limit=1)
    assert out == ["S"] and seen == {"ids": ["x"], "limit": 1}


async def test_data_reader_binding(monkeypatch) -> None:
    # Default (unbound) is the kernel-client reader; binding swaps it.
    monkeypatch.setattr(dr, "_reader", None)
    assert isinstance(dr.data_reader(), dr._KernelClientReader)
    bound = LocalDataServiceReader(_FakeStore())
    dr.bind_data_reader(bound)
    try:
        assert dr.data_reader() is bound
    finally:
        dr.bind_data_reader(None)
