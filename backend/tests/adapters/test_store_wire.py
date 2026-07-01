"""Phase B-A — store_wire codec round-trip (through a JSON boundary).

Each ``domain -> row -> json.dumps/loads -> row -> domain`` round-trip must be
lossless on the fields the kernel relies on. The JSON hop catches non-JSON-safe
values (e.g. tuples that must survive as lists).
"""

# ruff: noqa: I001 — boot.kernel side-effect import MUST precede src.* (sys.path)
from __future__ import annotations

import json

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect for src.*

from src.adapters import store_wire as sw
from src.core.agent_config import AgentConfig
from src.core.events import Event
from src.core.store_port import StoredEvent, UsageRollupRow
from src.core.types import (
    Message,
    ModelProvider,
    ModelSettings,
    Session,
    UserMessage,
)


def _json(row: dict) -> dict:
    return json.loads(json.dumps(row))


def test_session_round_trip():
    s = Session(
        id="sess-1",
        user_id="owner-1",
        agent_config=AgentConfig(id="ag", name="Agent", model="claude-sonnet-4-6"),
        cwd="/tmp/proj",
        runtime_provider="claude_agent",
        model="claude-sonnet-4-6",
        instructions="be terse",
        skills=("alpha", "beta"),
        model_provider=ModelProvider(base_url=None, api_key="sk-x", api_protocol="anthropic"),
        model_settings=ModelSettings(temperature=0.5, max_tokens=1000, effort="high"),
        permission_mode="full_access",
        mode="default",
        status="idle",
        created_at=123456789,
        metadata={"k": "v"},
        todos=[{"id": "1", "text": "do", "status": "pending"}],
    )
    s2 = sw.row_to_session(_json(sw.session_to_row(s)))

    assert s2.id == s.id
    assert s2.user_id == s.user_id
    assert s2.cwd == s.cwd
    assert s2.runtime_provider == s.runtime_provider
    assert s2.model == s.model
    assert s2.instructions == s.instructions
    assert s2.skills == ("alpha", "beta")  # tuple survives the JSON list hop
    assert s2.status == s.status
    assert s2.created_at == s.created_at
    assert s2.metadata == {"k": "v"}
    assert s2.agent_config.id == "ag" and s2.agent_config.name == "Agent"
    assert s2.model_provider is not None and s2.model_provider.api_key == "sk-x"
    assert s2.model_settings is not None and s2.model_settings.effort == "high"
    assert s2.todos == [{"id": "1", "text": "do", "status": "pending"}]


def test_message_round_trip():
    m = Message(
        id="msg-1",
        session_id="sess-1",
        user_message=UserMessage(text="hello"),
        started_at=42,
        status="completed",
        assistant_message="hi there",
        total_turns=3,
        input_tokens=10,
        output_tokens=20,
        ended_at=99,
        metadata={"a": 1},
    )
    m2 = sw.row_to_message(_json(sw.message_to_row(m)))

    assert m2.id == m.id
    assert m2.session_id == m.session_id
    assert m2.user_message.text == "hello"
    assert m2.status == "completed"
    assert m2.assistant_message == "hi there"
    assert m2.total_turns == 3
    assert m2.input_tokens == 10 and m2.output_tokens == 20
    assert m2.started_at == 42 and m2.ended_at == 99
    assert m2.metadata == {"a": 1}


def test_event_round_trip():
    e = Event(type="tool_use", data={"name": "bash", "input": {"cmd": "ls"}}, timestamp=7)
    e2 = sw.row_to_event(_json(sw.event_to_row(e)))
    assert e2.type == "tool_use"
    assert e2.data == {"name": "bash", "input": {"cmd": "ls"}}
    assert e2.timestamp == 7


def test_stored_event_round_trip():
    se = StoredEvent(
        seq=5,
        session_id="s",
        message_id="m",
        type="assistant_message",
        data={"text": "x"},
        timestamp=3,
    )
    se2 = sw.row_to_stored_event(_json(sw.stored_event_to_row(se)))
    assert se2 == se


def test_usage_rollup_round_trip():
    u = UsageRollupRow(
        day="2026-06-28",
        model="claude-sonnet-4-6",
        request_count=2,
        input_tokens=100,
        output_tokens=200,
        cache_read_tokens=5,
        cache_write_tokens=6,
    )
    u2 = sw.row_to_usage_rollup(_json(sw.usage_rollup_to_row(u)))
    assert u2 == u
