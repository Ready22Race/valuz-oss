"""KB auto-discovery — owner is derived from the KB row, not an ambient/device id.

The scheduler runs in a daemon THREAD with no inherited request context. It must
NOT scan as a single ambient/device owner (that only rescans one user's KBs on a
shared multi-user backend). Instead:

  * the scheduler enumerates auto-discover KBs owner-agnostically and calls
    ``service.rescan_kb(kb_id)`` per KB — it never touches the owner ContextVar;
  * ``rescan_kb`` derives the owner from the KB row itself (``get_kb_by_id`` →
    ``kb.user_id``) and threads it EXPLICITLY into the rescan task + the rescan
    work — it never reads or publishes the ambient owner ContextVar.

These tests pin both.
"""

from __future__ import annotations

import types
from contextlib import asynccontextmanager

import pytest

import valuz_agent.infra.db as db_mod
from valuz_agent.infra.auth_context import get_current_user_id
from valuz_agent.modules.docs import scheduler as sched
from valuz_agent.modules.docs.datastore import DocumentDatastore
from valuz_agent.modules.docs.service import DocumentLibraryService


class _KB:
    """Minimal stand-in — callers read only id / name / user_id."""

    def __init__(self, kb_id: str, name: str, user_id: str) -> None:
        self.id = kb_id
        self.name = name
        self.user_id = user_id


# ── scheduler: enumerate owner-agnostically, call rescan_kb(kb_id) per KB ──────


def _stub_heavy_scan(monkeypatch, tmp_path, kbs: list[_KB], called: list[str]) -> None:
    """Patch the heavy parser/service construction so the test isolates the
    scheduler loop. ``rescan_kb`` records the kb_id it was handed."""
    from valuz_agent.infra.config import settings as cfg

    async def _fake_list(self):  # type: ignore[no-untyped-def]
        return kbs

    monkeypatch.setattr(DocumentDatastore, "list_auto_discover_kbs", _fake_list)

    @asynccontextmanager
    async def _uow(*a, **k):  # type: ignore[no-untyped-def]
        yield None

    monkeypatch.setattr(db_mod, "async_unit_of_work", _uow)
    # ``docs_dir`` is a computed property (data_dir/"docs") — patch the field.
    monkeypatch.setattr(cfg, "data_dir", tmp_path)

    async def _async_none(*a, **k):  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr("valuz_agent.api.deps._parser_registry", lambda: None)
    monkeypatch.setattr("valuz_agent.api.deps._secret_store", lambda: None)
    monkeypatch.setattr("valuz_agent.api.deps._SecretStoreResolver", lambda *a, **k: None)
    monkeypatch.setattr(
        "valuz_agent.api.deps._setup_controller",
        lambda: types.SimpleNamespace(is_complete=lambda: True),
    )
    monkeypatch.setattr("valuz_agent.modules.parser.ParserRouter", lambda **k: None)
    monkeypatch.setattr(
        "valuz_agent.modules.settings.parser_routing.load_routing_config", _async_none
    )
    monkeypatch.setattr(
        "valuz_agent.integrations.docs_embedded.EmbeddedDocsRuntime", lambda **k: None
    )

    async def _rescan(self, kb_id: str):  # type: ignore[no-untyped-def]
        called.append(kb_id)
        return types.SimpleNamespace(total_items=0)

    monkeypatch.setattr(DocumentLibraryService, "rescan_kb", _rescan)


@pytest.mark.asyncio
async def test_scheduler_rescans_each_auto_discover_kb(monkeypatch, tmp_path) -> None:
    called: list[str] = []
    kbs = [_KB("kb-a", "A", "owner-A"), _KB("kb-b", "B", "owner-B")]
    _stub_heavy_scan(monkeypatch, tmp_path, kbs, called)

    await sched._arun_auto_discovery_scan()

    # Every owner's auto-discover KB is rescanned (by id) — not just one device id.
    assert called == ["kb-a", "kb-b"]


@pytest.mark.asyncio
async def test_no_kbs_is_a_noop(monkeypatch, tmp_path) -> None:
    called: list[str] = []
    _stub_heavy_scan(monkeypatch, tmp_path, [], called)
    await sched._arun_auto_discovery_scan()
    assert called == []


# ── rescan_kb: owner derived from the KB row ──────────────────────────────────


@pytest.mark.asyncio
async def test_rescan_kb_derives_owner_from_row(monkeypatch) -> None:
    """The owner is resolved from the KB row (``get_kb_by_id`` → ``kb.user_id``)
    and threaded EXPLICITLY into the rescan task + ``_run_rescan`` — the ambient
    owner ContextVar is never read or published."""
    svc = DocumentLibraryService.__new__(DocumentLibraryService)

    class _DS:
        async def get_kb_by_id(self, kb_id: str):  # type: ignore[no-untyped-def]
            return _KB(kb_id, "X", "owner-X")

    svc._ds = _DS()  # type: ignore[attr-defined]

    seen: dict[str, object] = {}

    async def _create_task(user_id: str, kb_id: str):  # type: ignore[no-untyped-def]
        seen["task_owner"] = user_id
        return types.SimpleNamespace(id="task-1", kb_id=kb_id, user_id=user_id)

    async def _run(kb, task):  # type: ignore[no-untyped-def]
        seen["rescan_owner"] = kb.user_id
        seen["ambient_during_run"] = get_current_user_id()

    monkeypatch.setattr(svc, "_create_rescan_task", _create_task)
    monkeypatch.setattr(svc, "_run_rescan", _run)
    monkeypatch.setattr("valuz_agent.modules.docs.service._task_to_result", lambda t: "ok")

    before = get_current_user_id()
    result = await svc.rescan_kb("kb-1")

    assert result == "ok"
    # Owner came from the KB row and was threaded explicitly into both the
    # rescan task and the rescan work.
    assert seen["task_owner"] == "owner-X"
    assert seen["rescan_owner"] == "owner-X"
    # The ambient ContextVar was never published — owner derivation is purely
    # entity-based (no set_current_user_id anywhere in the rescan path).
    assert seen["ambient_during_run"] == before
    assert get_current_user_id() == before


@pytest.mark.asyncio
async def test_rescan_kb_missing_kb_raises(monkeypatch) -> None:
    from valuz_agent.modules.docs.errors import KbNotFound

    svc = DocumentLibraryService.__new__(DocumentLibraryService)

    class _DS:
        async def get_kb_by_id(self, kb_id: str):  # type: ignore[no-untyped-def]
            return None

    svc._ds = _DS()  # type: ignore[attr-defined]

    with pytest.raises(KbNotFound):
        await svc.rescan_kb("nope")
