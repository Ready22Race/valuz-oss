"""Turn start announces ``running`` — the interim ``session_update``.

``run_turn`` persists ``session.status = "running"`` at turn entry but
historically emitted no event for it: the only ``session_update`` was the
terminal one after the turn (normally ``idle``). Every follower that derives
status from the event stream — the conversation header pill, the control
plane's ``run.status`` projection, per-turn re-subscribers on queue drains —
therefore sat on ``created``/stale for the whole turn and only caught up on a
manual refresh. These tests pin the fix: an interim
``session_update{status: running}`` is emitted right after ``user_message``
and BEFORE the runtime runs, and the terminal frame still closes the turn.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

import copy
import json

import valuz_agent.boot.kernel  # noqa: F401 — sets sys.path for ``src`` / ``app``

from src.core.agent_config import AgentConfig
from src.core.events import Event
from src.core.orchestrator import SessionOrchestrator
from src.core.types import (
    BARE_COMPLETION_METADATA_KEY,
    EndTurn,
    McpHttpServerConfig,
    Session,
    UserMessage,
)


class _FakeStore:
    """Just enough StorePort for one ``run_turn``: session load/save + the
    DatabaseEventSink append path (where the emitted events land)."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self.appended: list[Event] = []
        self._next_seq = 0

    async def load_session(self, user_id: str, session_id: str) -> Session | None:
        return self._session if session_id == self._session.id else None

    async def save_session(self, session: Session) -> None:
        self._session = session

    async def save_message(self, user_id: str, message: object) -> None:
        pass

    async def append_event(
        self, user_id: str, session_id: str, message_id: str, event: Event, **kw: object
    ) -> int:
        self.appended.append(event)
        self._next_seq += 1
        return self._next_seq


class _FakeRuntime:
    """RuntimePort stand-in that snapshots the persisted events at run() entry
    — proving the interim frame precedes the model turn, not just the return."""

    def __init__(self, store: _FakeStore) -> None:
        self._store = store
        self.types_at_run: list[str] | None = None
        self.has_live_background_tasks = False

    @property
    def approval_rule_matcher(self) -> object:
        return object()

    def update_sink(self, sink: object) -> None:
        pass

    def set_session_rule_finder(self, finder: object) -> None:  # pragma: no cover
        pass

    async def run(self, session: Session, user_message: UserMessage) -> None:
        self.types_at_run = [e.type for e in self._store.appended]
        session.status = "idle"

    async def interrupt(self) -> None:  # pragma: no cover
        pass

    async def close(self) -> None:
        pass


async def test_run_turn_emits_running_session_update_before_runtime(tmp_path, monkeypatch) -> None:
    agent = AgentConfig(id="agent-1", name="tester")
    session = Session(
        id="sess-1",
        agent_config=agent,
        cwd=str(tmp_path),
        user_id="owner-1",
        status="created",
    )
    store = _FakeStore(session)
    orch = SessionOrchestrator(store)  # type: ignore[arg-type]
    runtime = _FakeRuntime(store)
    monkeypatch.setattr("src.runtimes.factory.create_runtime", lambda *a, **k: runtime)

    message = await orch.run_turn("owner-1", "sess-1", UserMessage(text="hi"))

    types = [e.type for e in store.appended]
    # Interim frame right after the start marker...
    assert types[:2] == ["user_message", "session_update"]
    running = store.appended[1]
    assert running.data["status"] == "running"
    assert running.data["message_id"] == message.id
    # ...and already durable BEFORE the runtime ran a single token.
    assert runtime.types_at_run == ["user_message", "session_update"]
    # The terminal frame still closes the turn with the post-turn status.
    terminal = store.appended[-1]
    assert terminal.type == "session_update"
    assert terminal.data["status"] == "idle"


class _CitationRepairRuntime:
    def __init__(self, sink: object) -> None:
        self.sink = sink
        self.prompts: list[str] = []
        self.sessions: list[Session] = []
        self.closed = False
        self.has_live_background_tasks = False

    @property
    def approval_rule_matcher(self) -> object:
        return object()

    def update_sink(self, sink: object) -> None:
        self.sink = sink

    async def run(self, session: Session, user_message: UserMessage) -> None:
        self.prompts.append(user_message.text)
        self.sessions.append(copy.deepcopy(session))
        is_repair = bool(session.metadata.get(BARE_COMPLETION_METADATA_KEY))
        if not is_repair:
            evidence = {
                "_valuz_evidence": {
                    "evidenceHandle": "ev_repair_12345678",
                    "source": {
                        "sourceId": "doc-1",
                        "providerId": "docs",
                        "documentId": "doc-1",
                        "sourceType": "document",
                        "title": "Report",
                        "retrievedAt": "2026-07-30T10:00:00Z",
                    },
                    "evidence": {
                        "kind": "text",
                        "quote": "Revenue increased by 12%.",
                        "snippet": "Revenue increased by 12%.",
                        "capturedAt": "2026-07-30T10:00:00Z",
                    },
                    "locator": {"kind": "pdf", "page": 1},
                }
            }
            await self.sink.emit(
                Event(type="tool_use", data={"id": "tool-1", "name": "doc_search"})
            )
            await self.sink.emit(
                Event(
                    type="tool_result",
                    data={"id": "tool-1", "content": json.dumps(evidence)},
                )
            )
            answer = "Revenue declined."
            session.runtime_session_id = "native-research-thread"
        else:
            context = json.loads(
                user_message.text.split("Restricted repair context (JSON):\n", 1)[1]
            )
            answer = json.dumps(
                {
                    "version": "citation-claim-patch-v1",
                    "patches": [
                        {
                            "claimId": context["claimIssues"][0]["claimId"],
                            "replacementText": "Revenue increased by 12%.",
                            "evidenceHandles": ["ev_repair_12345678"],
                        }
                    ],
                }
            )
        await self.sink.emit(Event(type="assistant_message", data={"text": answer}))
        session.status = "idle"
        session.stop_reason = EndTurn()
        await self.sink.emit(
            Event(
                type="session_idle",
                data={"stop_reason": {"type": "end_turn"}, "num_turns": 1},
            )
        )

    async def interrupt(self) -> None:  # pragma: no cover
        pass

    async def close(self) -> None:
        self.closed = True


async def test_continuation_uses_refreshed_mcp_snapshot_and_keeps_it_in_memory(
    tmp_path,
) -> None:
    agent = AgentConfig(id="agent-1", name="tester")
    stale = McpHttpServerConfig(
        name="reportify",
        url="https://mcp.example.test",
        headers={"Authorization": "Bearer stale"},
    )
    fresh = McpHttpServerConfig(
        name="reportify",
        url="https://mcp.example.test",
        headers={"Authorization": "Bearer fresh"},
    )
    session = Session(
        id="sess-refresh-continuation",
        agent_config=agent,
        cwd=str(tmp_path),
        user_id="owner-1",
        status="running",
        mcp_servers=(stale,),
    )
    store = _FakeStore(session)
    orch = SessionOrchestrator(store)  # type: ignore[arg-type]

    async def refresh(user_id: str, session_id: str) -> bool:
        assert (user_id, session_id) == ("owner-1", session.id)
        durable = copy.deepcopy(session)
        durable.mcp_servers = (fresh,)
        store._session = durable
        return True

    orch.set_citation_repair_refresh_hook(refresh)

    await orch._refresh_continuation_credentials(  # noqa: SLF001
        "owner-1",
        session.id,
        session,
        continuation="task coverage",
    )

    assert session.mcp_servers == (fresh,)
    await store.save_session(session)
    assert store._session.mcp_servers == (fresh,)


async def test_run_turn_does_not_repair_an_unresolved_claim(tmp_path, monkeypatch) -> None:
    agent = AgentConfig(id="agent-1", name="tester")
    session = Session(
        id="sess-1",
        agent_config=agent,
        cwd=str(tmp_path),
        user_id="owner-1",
        status="created",
        skills=("/bundled/skills/citation",),
        metadata={"valuz": {"citation_verification_enabled": True}},
    )
    store = _FakeStore(session)
    orch = SessionOrchestrator(store)  # type: ignore[arg-type]
    runtimes: list[_CitationRepairRuntime] = []

    def create_runtime(*args, **kwargs) -> _CitationRepairRuntime:  # noqa: ANN002, ANN003
        runtime = _CitationRepairRuntime(args[2])
        runtimes.append(runtime)
        return runtime

    monkeypatch.setattr("src.runtimes.factory.create_runtime", create_runtime)

    message = await orch.run_turn(
        "owner-1",
        "sess-1",
        UserMessage(text="Answer with citations"),
    )

    assert len(runtimes) == 1
    assert len(runtimes[0].prompts) == 1
    assert runtimes[0].prompts[0] == "Answer with citations"
    assert runtimes[0].closed is False
    assert store._session.runtime_session_id == "native-research-thread"
    assert message.assistant_message is not None
    assert "Revenue declined." in message.assistant_message
    assert [event.type for event in store.appended].count("assistant_message") == 1
    assert [event.type for event in store.appended].count("session_idle") == 1


async def test_unresolved_claim_does_not_refresh_credentials_for_repair(
    tmp_path, monkeypatch
) -> None:
    agent = AgentConfig(id="agent-1", name="tester")
    session = Session(
        id="sess-refresh",
        agent_config=agent,
        cwd=str(tmp_path),
        user_id="owner-1",
        status="created",
        skills=("/bundled/skills/citation",),
        metadata={"valuz": {"citation_verification_enabled": True}},
    )
    store = _FakeStore(session)
    orch = SessionOrchestrator(store)  # type: ignore[arg-type]
    runtimes: list[_CitationRepairRuntime] = []
    refresh_calls: list[tuple[str, str]] = []

    async def refresh(user_id: str, session_id: str) -> bool:
        refresh_calls.append((user_id, session_id))
        return True

    orch.set_citation_repair_refresh_hook(refresh)

    def create_runtime(*args, **kwargs) -> _CitationRepairRuntime:  # noqa: ANN002, ANN003
        runtime = _CitationRepairRuntime(args[2])
        runtimes.append(runtime)
        return runtime

    monkeypatch.setattr("src.runtimes.factory.create_runtime", create_runtime)

    message = await orch.run_turn(
        "owner-1",
        "sess-refresh",
        UserMessage(text="Answer with citations"),
    )

    assert refresh_calls == []
    assert len(runtimes) == 1
    assert runtimes[0].prompts == ["Answer with citations"]
    assert message.assistant_message is not None
    assert "Revenue declined." in message.assistant_message
