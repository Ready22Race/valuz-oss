"""VALUZ-CHATPLAN S4 — inject_into_task tests.

Exercises ``TaskOrchestrator.inject_into_task`` directly against a tmp SQLite
fixture (no kernel session bring-up needed). Pattern mirrors
``test_chatplan_s2.py``.
"""

# ruff: noqa: I001
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import valuz_agent.boot.kernel  # noqa: F401
from sqlalchemy import select

from valuz_agent.modules.tasks import messaging
from valuz_agent.modules.tasks.mailbox import mailbox_registry
from valuz_agent.modules.tasks.models import TaskEventRow, TaskRow, TaskSessionRow


LOCAL_USER_ID = "local-test-owner"




@pytest.fixture(autouse=True)
def _reset_mailbox():
    """Each test starts with an empty mailbox registry."""
    mailbox_registry._boxes.clear()
    yield
    mailbox_registry._boxes.clear()


def _events(db_factory) -> list[TaskEventRow]:
    db = db_factory()
    try:
        return list(
            db.execute(select(TaskEventRow).order_by(TaskEventRow.sequence)).scalars().all()
        )
    finally:
        db.close()


def _seed_task(
    db_factory,
    tmp_path,
    *,
    task_id: str = "t1",
    project_id: str = "w1",
    status: str = "active",
    originator: str = "chat-session-1",
    lead_session_id: str | None = "lead-sess-1",
) -> None:
    """Insert a task row + (optionally) its lead run row."""
    db = db_factory()
    try:
        task = TaskRow(
            user_id="local-test-owner",
            id=task_id,
            project_id=project_id,
            file_path=str(tmp_path / f"{task_id}.md"),
            title="T",
            goal="do it",
            status=status,
            created_by="user",
            lead_agent_slug="lead-agent",
            current_holder=lead_session_id or "lead-agent",
            metadata_={"originating_session_id": originator},
        )
        db.add(task)
        if lead_session_id is not None:
            run = TaskSessionRow(
                user_id="local-test-owner",
                project_id=project_id,
                task_id=task_id,
                session_id=lead_session_id,
                agent_slug="lead-agent",
                sequence=0,
                kind="lead",
                status="active",
                label="Kickoff",
                goal="do it",
                project_mode="shared",
                run_dir=str(tmp_path),
            )
            db.add(run)
        db.commit()
    finally:
        db.close()


# ── happy path: active task + registered lead inbox ─────────────────────


def test_inject_into_active_task_with_registered_lead_delivers(db_factory, tmp_path):
    _seed_task(db_factory, tmp_path, status="active")
    mailbox_registry.register("lead-sess-1")
    result = asyncio.run(
        messaging.inject_into_task(
            task_id="t1",
            project_id="w1",
            text="actually focus on Q4 earnings",
            from_session_id="chat-session-1",
            user_id=LOCAL_USER_ID,
        )
    )
    assert result["delivered"] is True
    assert result["lead_session_id"] == "lead-sess-1"
    assert result["reason"] is None


def test_inject_appends_user_inject_event_on_delivery(db_factory, tmp_path):
    _seed_task(db_factory, tmp_path, status="active")
    mailbox_registry.register("lead-sess-1")
    asyncio.run(
        messaging.inject_into_task(
            task_id="t1",
            project_id="w1",
            text="hello lead",
            from_session_id="chat-session-1",
            user_id=LOCAL_USER_ID,
        )
    )
    events = _events(db_factory)
    types = [e.type for e in events]
    assert "user_inject" in types
    user_inject = next(e for e in events if e.type == "user_inject")
    assert user_inject.actor == "chat-session-1"
    assert user_inject.session_id == "lead-sess-1"
    assert user_inject.payload["text"] == "hello lead"
    assert user_inject.payload["lead_session_id"] == "lead-sess-1"


def test_inject_queues_wrapped_message_in_lead_mailbox(db_factory, tmp_path):
    _seed_task(db_factory, tmp_path, status="active")
    mailbox_registry.register("lead-sess-1")
    asyncio.run(
        messaging.inject_into_task(
            task_id="t1",
            project_id="w1",
            text="please pivot to Q4",
            from_session_id="chat-session-1",
            user_id=LOCAL_USER_ID,
        )
    )
    box = mailbox_registry._boxes["lead-sess-1"]
    assert box.qsize() == 1
    msg = box.get_nowait()
    assert msg.kind == "text"
    assert msg.from_session == "chat-session-1"
    assert '<user-instruction source="chat">' in msg.text
    assert "please pivot to Q4" in msg.text
    assert "</user-instruction>" in msg.text


# ── lead offline (mailbox unregistered) ─────────────────────────────────


def test_inject_into_active_task_with_offline_lead_drops(db_factory, tmp_path):
    _seed_task(db_factory, tmp_path, status="active")
    # NOTE: deliberately do NOT register the lead mailbox — simulates a
    # crashed-and-not-yet-resumed actor loop.
    result = asyncio.run(
        messaging.inject_into_task(
            task_id="t1",
            project_id="w1",
            text="hi",
            from_session_id="chat-session-1",
            user_id=LOCAL_USER_ID,
        )
    )
    assert result["delivered"] is False
    assert result["reason"] == "LEAD_OFFLINE"
    assert result["lead_session_id"] == "lead-sess-1"


def test_inject_offline_lead_appends_user_inject_dropped_event(db_factory, tmp_path):
    _seed_task(db_factory, tmp_path, status="active")
    asyncio.run(
        messaging.inject_into_task(
            task_id="t1",
            project_id="w1",
            text="hi",
            from_session_id="chat-session-1",
            user_id=LOCAL_USER_ID,
        )
    )
    events = _events(db_factory)
    types = [e.type for e in events]
    assert "user_inject_dropped" in types
    assert "user_inject" not in types
    dropped = next(e for e in events if e.type == "user_inject_dropped")
    assert dropped.payload["reason"] == "LEAD_OFFLINE"


# ── status gate ─────────────────────────────────────────────────────────


def test_inject_into_draft_task_rejects_with_task_not_active(db_factory, tmp_path):
    _seed_task(db_factory, tmp_path, status="draft", lead_session_id=None)
    result = asyncio.run(
        messaging.inject_into_task(
            task_id="t1",
            project_id="w1",
            text="hi",
            from_session_id="chat-session-1",
            user_id=LOCAL_USER_ID,
        )
    )
    assert result["delivered"] is False
    assert result["reason"] == "TASK_NOT_ACTIVE"
    assert result["lead_session_id"] is None
    # And no event was appended.
    assert _events(db_factory) == []


def test_inject_into_completed_task_rejects(db_factory, tmp_path):
    _seed_task(db_factory, tmp_path, status="completed")
    mailbox_registry.register("lead-sess-1")
    result = asyncio.run(
        messaging.inject_into_task(
            task_id="t1",
            project_id="w1",
            text="hi",
            from_session_id="chat-session-1",
            user_id=LOCAL_USER_ID,
        )
    )
    assert result["delivered"] is False
    assert result["reason"] == "TASK_NOT_ACTIVE"


def test_inject_reports_halted_instead_of_reviving(db_factory, tmp_path):
    """The DELIVERY layer reports the state; it does not orchestrate.

    Injecting into a halted task can't be delivered — the lead loop is torn
    down and its mailbox unregistered. ``messaging`` says so and stops there:
    deciding to revive is orchestration, and a leaf delivery helper reaching up
    to the composition root for ``resume_task`` was an inverted dependency
    (masked by a function-local import). The revive itself is asserted by
    ``test_inject_handler_revives_halted_task`` below, at the layer that owns
    the decision.
    """
    for i, status in enumerate(("stopped", "paused", "blocked")):
        tid = f"t-halted-{i}"
        _seed_task(db_factory, tmp_path, task_id=tid, status=status)
        result = asyncio.run(
            messaging.inject_into_task(
                task_id=tid,
                project_id="w1",
                text="继续,并且优先做数据核对",
                from_session_id="chat-session-1",
                user_id=LOCAL_USER_ID,
            )
        )
        assert result["delivered"] is False, status
        assert result["reason"] == "TASK_HALTED", status
        assert result["task_status"] == status


def test_inject_handler_revives_halted_task(db_factory, tmp_path, monkeypatch):
    """"说句话就能继续" — end to end, at the layer that decides it.

    The chat-facing ``inject_into_task`` tool turns the delivery layer's
    TASK_HALTED into a ``resume_task`` carrying the text as the resume
    instruction, so the behaviour users rely on is unchanged by the move.
    """
    from valuz_agent.adapters import data_reader as dr_mod
    from valuz_agent.integrations.toolkit_mcp_server import HostExecContext
    from valuz_agent.modules.tasks.tools import handlers as h_mod

    _seed_task(db_factory, tmp_path, status="stopped")
    calls: list[dict] = []

    class _Orch:
        async def resume_task(self, task_id, project_id, **kw):
            calls.append({"task_id": task_id, "project_id": project_id, **kw})
            return {"ok": True, "prior_status": "stopped", "resumed": True}

    class _Reader:
        async def get_session(self, _uid, _sid):
            return SimpleNamespace(
                id="chat-session-1", project_id="w1", metadata={"valuz": {}}
            )

    monkeypatch.setattr(dr_mod, "data_reader", lambda: _Reader())
    monkeypatch.setattr(h_mod, "data_reader", lambda: _Reader())

    res = asyncio.run(
        h_mod._inject_into_task_handler(
            _Orch(),
            {"task_id": "t1", "text": "继续,并且优先做数据核对"},
            HostExecContext(session_id="chat-session-1", user_id=LOCAL_USER_ID),
        )
    )
    assert not res.is_error
    assert "TASK_RESUMED" in res.content
    assert calls and calls[0]["instruction"] == "继续,并且优先做数据核对"



# ── no lead run row at all ──────────────────────────────────────────────


def test_inject_with_no_lead_run_returns_no_lead(db_factory, tmp_path):
    # Active task but no lead session row (shouldn't normally happen — defensive).
    _seed_task(db_factory, tmp_path, status="active", lead_session_id=None)
    result = asyncio.run(
        messaging.inject_into_task(
            task_id="t1",
            project_id="w1",
            text="hi",
            from_session_id="chat-session-1",
            user_id=LOCAL_USER_ID,
        )
    )
    assert result["delivered"] is False
    assert result["reason"] == "NO_LEAD"
    assert result["lead_session_id"] is None


# ── wrapped text envelope ───────────────────────────────────────────────


def test_wrapped_envelope_uses_user_instruction_source_chat_tag(db_factory, tmp_path):
    _seed_task(db_factory, tmp_path, status="active")
    mailbox_registry.register("lead-sess-1")
    asyncio.run(
        messaging.inject_into_task(
            task_id="t1",
            project_id="w1",
            text="raw user text",
            from_session_id="chat-session-1",
            user_id=LOCAL_USER_ID,
        )
    )
    msg = mailbox_registry._boxes["lead-sess-1"].get_nowait()
    # The raw text is preserved inside the envelope (no escaping shenanigans).
    expected = '<user-instruction source="chat">\nraw user text\n</user-instruction>'
    assert msg.text == expected
