"""Session input queue — datastore + pause-marker behaviour.

Pins the durable-queue primitives the host-driven drain builds on
(docs/design/session-input-queue.md): FIFO ordering, soft-cap counting,
edit-only-while-queued, delete, the SYSTEM drain hooks (peek/mark), the
listed-status filter, and the per-session pause marker.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.infra.database import Base
from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.sessions import project_index
from valuz_agent.modules.sessions.datastore import SessionDatastore
from valuz_agent.modules.sessions.models import (
    ProjectSessionRow,
    QueuedInputRow,
    SessionAttachmentRow,
)

OWNER = "local-test-owner"  # matches tests/conftest.py autouse owner


@pytest.fixture(autouse=True)
def _queue_db(tmp_path, monkeypatch):
    """Tmp SQLite with the queue / attachment / index tables; UoW bound to it."""
    import valuz_agent.infra.db as db_mod

    db_file = tmp_path / "queue.db"
    sync_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(
        sync_engine,
        tables=[
            QueuedInputRow.__table__,
            SessionAttachmentRow.__table__,
            ProjectSessionRow.__table__,
        ],
    )
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    monkeypatch.setattr(
        db_mod,
        "AsyncSessionLocal",
        async_sessionmaker(bind=async_engine, expire_on_commit=False),
    )


def _row(session_id: str, text: str) -> QueuedInputRow:
    return QueuedInputRow(
        session_id=session_id,
        project_id="proj-1",
        input={"text": text, "attachments": []},
        status="queued",
    )


async def test_enqueue_is_fifo_with_monotonic_position() -> None:
    async with async_unit_of_work() as db:
        ds = SessionDatastore(db)
        for t in ("first", "second", "third"):
            await ds.create_queued(OWNER, _row("s1", t))

    async with async_unit_of_work(commit=False) as db:
        rows = await SessionDatastore(db).list_queued(OWNER, "s1")
    assert [r.input["text"] for r in rows] == ["first", "second", "third"]
    assert [r.position for r in rows] == [0, 1, 2]


async def test_count_and_peek_and_mark_dispatched() -> None:
    async with async_unit_of_work() as db:
        ds = SessionDatastore(db)
        await ds.create_queued(OWNER, _row("s2", "a"))
        await ds.create_queued(OWNER, _row("s2", "b"))

    async with async_unit_of_work(commit=False) as db:
        assert await SessionDatastore(db).count_queued(OWNER, "s2") == 2

    # SYSTEM drain: peek oldest, dispatch it, peek again → next one.
    async with async_unit_of_work() as db:
        ds = SessionDatastore(db)
        head = await ds.peek_next_queued("s2")
        assert head is not None and head.input["text"] == "a"
        await ds.mark_queued_status(head.id, "dispatched")

    async with async_unit_of_work() as db:
        ds = SessionDatastore(db)
        head2 = await ds.peek_next_queued("s2")
        assert head2 is not None and head2.input["text"] == "b"
    # dispatched item drops out of the user-visible list + the cap count.
    async with async_unit_of_work(commit=False) as db:
        ds = SessionDatastore(db)
        assert await ds.count_queued(OWNER, "s2") == 1
        assert [r.input["text"] for r in await ds.list_queued(OWNER, "s2")] == ["b"]


async def test_blocked_items_are_listed_but_not_counted() -> None:
    async with async_unit_of_work() as db:
        ds = SessionDatastore(db)
        await ds.create_queued(OWNER, _row("s3", "x"))
        head = await ds.peek_next_queued("s3")
        assert head is not None
        await ds.mark_queued_status(head.id, "blocked", error_message="insufficient_credits")

    async with async_unit_of_work(commit=False) as db:
        ds = SessionDatastore(db)
        listed = await ds.list_queued(OWNER, "s3")
        assert len(listed) == 1 and listed[0].status == "blocked"
        assert listed[0].error_message == "insufficient_credits"
        # blocked is visible but not a "still queued" item for the soft cap.
        assert await ds.count_queued(OWNER, "s3") == 0


async def test_edit_only_while_queued() -> None:
    async with async_unit_of_work() as db:
        ds = SessionDatastore(db)
        await ds.create_queued(OWNER, _row("s4", "orig"))
        row = await ds.peek_next_queued("s4")
        assert row is not None
        qid = row.id

    # edit a queued row → succeeds
    async with async_unit_of_work() as db:
        ds = SessionDatastore(db)
        updated = await ds.update_queued_input(OWNER, "s4", qid, {"text": "edited", "attachments": []})
        assert updated is not None and updated.input["text"] == "edited"

    # dispatch it, then an edit attempt is a no-op (returns None)
    async with async_unit_of_work() as db:
        ds = SessionDatastore(db)
        await ds.mark_queued_status(qid, "dispatched")
    async with async_unit_of_work() as db:
        ds = SessionDatastore(db)
        assert await ds.update_queued_input(OWNER, "s4", qid, {"text": "nope"}) is None


async def test_delete_and_owner_scoping() -> None:
    async with async_unit_of_work() as db:
        ds = SessionDatastore(db)
        await ds.create_queued(OWNER, _row("s5", "del-me"))
        row = await ds.peek_next_queued("s5")
        assert row is not None
        qid = row.id

    async with async_unit_of_work() as db:
        ds = SessionDatastore(db)
        # wrong owner → not found, no delete
        assert await ds.delete_queued("someone-else", "s5", qid) is False
        assert await ds.delete_queued(OWNER, "s5", qid) is True
        assert await ds.delete_queued(OWNER, "s5", qid) is False  # already gone


async def test_list_queued_session_owners_for_boot_recovery() -> None:
    async with async_unit_of_work() as db:
        ds = SessionDatastore(db)
        await ds.create_queued(OWNER, _row("s6", "a"))
        await ds.create_queued(OWNER, _row("s7", "b"))
        # a dispatched-only session must not appear
        await ds.create_queued(OWNER, _row("s8", "c"))
        done = await ds.peek_next_queued("s8")
        assert done is not None
        await ds.mark_queued_status(done.id, "dispatched")

    async with async_unit_of_work(commit=False) as db:
        pairs = await SessionDatastore(db).list_queued_session_owners()
    sessions = {sid for sid, _ in pairs}
    assert sessions == {"s6", "s7"}
    assert all(uid == OWNER for _, uid in pairs)


async def test_queue_pause_marker_roundtrip() -> None:
    await project_index.record("proj-1", "s9", kind="chat")
    assert await project_index.get_queue_paused_at("s9") is None

    await project_index.set_queue_paused("s9", True)
    paused_at = await project_index.get_queue_paused_at("s9")
    assert isinstance(paused_at, int) and paused_at > 0

    await project_index.set_queue_paused("s9", False)
    assert await project_index.get_queue_paused_at("s9") is None


# ---- Drain engine (_drain_queue_after_turn) ----


class _FakeBus:
    def __init__(self) -> None:
        self.events: list = []

    def publish(self, *args, **kwargs) -> None:
        self.events.append((args, kwargs))


class _FakeSession:
    status = "idle"
    metadata = {"valuz": {"project_id": "proj-1"}}


def _patch_drain(monkeypatch, *, budget_raises=False):
    """Stub the drain's external deps; return the list of dispatched texts."""
    import valuz_agent.adapters.kernel_client as kc
    import valuz_agent.modules.sessions.run_orchestrator as run_orchestrator
    import valuz_agent.modules.sessions.service as svc

    calls: list[str] = []

    async def _fake_run(session_id, text, event_bus, on_message=None, queued_attachments=None):
        calls.append(text)
        return "idle"

    async def _fake_get_session(uid, sid):
        return _FakeSession()

    async def _budget(session):
        if budget_raises:
            from valuz_agent.modules.sessions.errors import BudgetExceeded

            raise BudgetExceeded("no credits", message_key="insufficient_credits")

    # ``run_session_to_idle`` is bound at import time in run_orchestrator's
    # namespace (module-level import), so patch it there, not on actor_runner.
    monkeypatch.setattr(run_orchestrator, "run_session_to_idle", _fake_run)
    monkeypatch.setattr(kc, "get_session", _fake_get_session)
    monkeypatch.setattr(svc, "_enforce_budget", _budget)
    return calls


async def test_drain_runs_queued_items_fifo(monkeypatch) -> None:
    from valuz_agent.modules.sessions import run_orchestrator

    async with async_unit_of_work() as db:
        ds = SessionDatastore(db)
        await ds.create_queued(OWNER, _row("d1", "one"))
        await ds.create_queued(OWNER, _row("d1", "two"))

    calls = _patch_drain(monkeypatch)
    await run_orchestrator._drain_queue_after_turn("d1", _FakeBus())

    assert calls == ["one", "two"]
    async with async_unit_of_work(commit=False) as db:
        assert await SessionDatastore(db).count_queued(OWNER, "d1") == 0


async def test_drain_blocks_first_item_on_budget(monkeypatch) -> None:
    from valuz_agent.modules.sessions import run_orchestrator

    async with async_unit_of_work() as db:
        ds = SessionDatastore(db)
        await ds.create_queued(OWNER, _row("d2", "one"))
        await ds.create_queued(OWNER, _row("d2", "two"))

    calls = _patch_drain(monkeypatch, budget_raises=True)
    await run_orchestrator._drain_queue_after_turn("d2", _FakeBus())

    assert calls == []  # budget pre-check failed → nothing dispatched
    async with async_unit_of_work(commit=False) as db:
        rows = await SessionDatastore(db).list_queued(OWNER, "d2")
    by_text = {r.input["text"]: r.status for r in rows}
    assert by_text == {"one": "blocked", "two": "queued"}


async def test_drain_skips_when_paused(monkeypatch) -> None:
    from valuz_agent.modules.sessions import run_orchestrator

    await project_index.record("proj-1", "d3", kind="chat")
    async with async_unit_of_work() as db:
        await SessionDatastore(db).create_queued(OWNER, _row("d3", "one"))
    await project_index.set_queue_paused("d3", True)

    calls = _patch_drain(monkeypatch)
    await run_orchestrator._drain_queue_after_turn("d3", _FakeBus())

    assert calls == []  # paused → drain returns without running
    async with async_unit_of_work(commit=False) as db:
        assert await SessionDatastore(db).count_queued(OWNER, "d3") == 1
