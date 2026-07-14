"""genui runner — ephemeral-session completer tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import valuz_agent.boot.kernel  # noqa: F401  (sets kernel import path)
import valuz_agent.modules.genui.runner as r
from valuz_agent.modules.genui.runner import _resolve_provider_id


def test_resolve_provider_id_prefers_locked():
    src = SimpleNamespace(
        metadata={"valuz": {"locked_provider_id": "p1"}},
        agent_config=SimpleNamespace(metadata={"provider_id": "p2"}),
    )
    assert _resolve_provider_id(src) == "p1"


def test_resolve_provider_id_falls_back_to_agent_config():
    src = SimpleNamespace(
        metadata={"valuz": {}},
        agent_config=SimpleNamespace(metadata={"provider_id": "p2"}),
    )
    assert _resolve_provider_id(src) == "p2"


def test_resolve_provider_id_none_when_missing():
    src = SimpleNamespace(metadata={"valuz": {}}, agent_config=SimpleNamespace(metadata={}))
    assert _resolve_provider_id(src) is None


@pytest.fixture
def patched(tmp_path, monkeypatch):
    """Stub kernel_client + fs_registry so _make_completer runs without a kernel."""
    monkeypatch.setattr(r.fs_registry, "data_dir", lambda user_id: tmp_path / "app")

    captured: dict = {}

    async def _create(user_id, req):
        captured["req"] = req
        captured.setdefault("create_reqs", []).append(req)

    async def _run_turn(user_id, sid, prompt):
        captured["prompt"] = prompt
        return SimpleNamespace(assistant_message="Chart\n  data: 1,2,3")

    async def _delete(user_id, sid):
        captured.setdefault("deleted", []).append(sid)

    async def _gen():
        for d in ({"text": "root "}, {"text": "= Stack()"}):
            yield SimpleNamespace(type="text_delta", data=d)
        yield SimpleNamespace(type="assistant_message", data={"text": "Chart\n  data: 1,2,3"})

    def _subscribe(user_id, sid):
        captured.setdefault("subscribed", []).append(sid)
        return _gen()

    async def _emit(user_id, sid, type_, data):
        captured.setdefault("forwarded", []).append((sid, type_, data))

    monkeypatch.setattr(r.kernel_client, "create_session", _create)
    monkeypatch.setattr(r.kernel_client, "run_turn", _run_turn)
    monkeypatch.setattr(r.kernel_client, "delete_session", _delete)
    monkeypatch.setattr(r.kernel_client, "subscribe_session_events", _subscribe)
    monkeypatch.setattr(r.kernel_client, "emit_live_event", _emit)
    return captured


async def test_completer_builds_ephemeral_session_and_returns_text(patched):
    completer = r._make_completer(
        user_id="u1", runtime_provider="claude_agent", model="claude-sonnet-4-6", mp=None
    )
    out = await completer("PROMPT")
    assert out == "Chart\n  data: 1,2,3"
    req = patched["req"]
    assert req.id  # ephemeral id set
    assert req.model == "claude-sonnet-4-6"
    assert req.runtime_provider == "claude_agent"
    assert req.model_provider is None  # mp=None → OAuth-style self-auth
    assert req.metadata == {"valuz": {"ephemeral_generative_ui": True}}
    assert "OpenUI Lang" in req.instructions
    assert patched["prompt"] == "PROMPT"
    assert patched["deleted"] == [req.id]  # cleanup ran


async def test_generative_ui_sessions_share_one_fixed_cwd(patched):
    """Runtimes key per-project artifacts on the session cwd (claude-agent-sdk
    keeps transcripts under ~/.claude/projects/<encoded-cwd>/). A per-call cwd
    would leak one such directory per generation — every generative-UI session
    must share ONE fixed cwd, identical across calls and free of the session id."""
    completer = r._make_completer(
        user_id="u1", runtime_provider="claude_agent", model="claude-sonnet-4-6", mp=None
    )
    await completer("PROMPT-1")
    await completer("PROMPT-2")
    reqs = patched["create_reqs"]
    assert len(reqs) == 2
    assert reqs[0].cwd == reqs[1].cwd
    assert reqs[0].cwd.endswith("generative-ui")
    assert reqs[0].id not in reqs[0].cwd and reqs[1].id not in reqs[1].cwd


async def test_completer_streams_text_deltas_to_calling_session(patched):
    """tool_use_id 非空时,订阅 ephemeral 的 text_delta,转发成调用方 session
    的 tool_output_delta(keyed by tool_use_id);run_turn 全文仍作为返回值。"""
    completer = r._make_completer(
        user_id="u1",
        runtime_provider="claude_agent",
        model="claude-sonnet-4-6",
        mp=None,
        calling_session_id="calling-sid",
        tool_use_id="R1",
    )
    out = await completer("PROMPT")
    assert out == "Chart\n  data: 1,2,3"  # run_turn 全文(canonical)
    forwarded = patched.get("forwarded", [])
    assert forwarded == [
        ("calling-sid", "tool_output_delta", {"id": "R1", "text": "root "}),
        ("calling-sid", "tool_output_delta", {"id": "R1", "text": "= Stack()"}),
    ]
    assert patched["deleted"] == [patched["req"].id]  # cleanup 仍跑


async def test_completer_sync_when_no_tool_use_id(patched):
    """tool_use_id=None → 不订阅、不转发,纯同步(行为同同步版)。"""
    completer = r._make_completer(
        user_id="u1",
        runtime_provider="claude_agent",
        model="claude-sonnet-4-6",
        mp=None,
        calling_session_id="calling-sid",
        tool_use_id=None,
    )
    out = await completer("PROMPT")
    assert out == "Chart\n  data: 1,2,3"
    assert patched.get("forwarded", []) == []
    assert patched.get("subscribed", []) == []  # 没订阅
