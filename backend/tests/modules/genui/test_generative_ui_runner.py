"""genui runner — ephemeral-session completer tests."""

from __future__ import annotations

import logging
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


def test_direct_anthropic_deepseek_model_disables_thinking():
    mp = SimpleNamespace(
        base_url="https://api.deepseek.com/anthropic/v1/messages",
        api_key="k",
        api_protocol="anthropic",
    )

    chat_model = r._build_direct_chat_model(model="deepseek-v4-flash", mp=mp)

    assert chat_model.thinking == {"type": "disabled"}
    assert chat_model.max_tokens == 16384


def test_direct_anthropic_non_deepseek_model_does_not_force_thinking():
    mp = SimpleNamespace(
        base_url="https://api.anthropic.com",
        api_key="k",
        api_protocol="anthropic",
    )

    chat_model = r._build_direct_chat_model(model="claude-sonnet-4-6", mp=mp)

    assert chat_model.thinking is None


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


async def test_completer_uses_direct_llm_for_non_official_api_key_provider(
    patched, monkeypatch, caplog
):
    """非 Claude/Codex 官方订阅路径有显式 credential，不能再创建
    ephemeral session；直接调用模型流，并把 chunk 转给调用方 tool card。"""

    class _FakeChatModel:
        async def astream(self, messages):
            patched["direct_messages"] = messages
            yield SimpleNamespace(content="root ")
            yield SimpleNamespace(content=[{"type": "text", "text": "= Stack()"}])

    monkeypatch.setattr(
        r,
        "_build_direct_chat_model",
        lambda *, model, mp: _FakeChatModel(),
    )
    mp = SimpleNamespace(
        base_url="https://example.test/v1",
        api_key="k",
        api_protocol="openai_response",
    )
    completer = r._make_completer(
        user_id="u1",
        runtime_provider="codex",
        model="gpt-5-codex-api-key",
        mp=mp,
        calling_session_id="calling-sid",
        tool_use_id="R1",
    )

    with caplog.at_level(logging.INFO, logger=r.__name__):
        out = await completer("PROMPT")

    assert out == "root = Stack()"
    assert "req" not in patched
    assert patched.get("deleted", []) == []
    assert patched["direct_messages"][0].content == "PROMPT"
    assert patched["forwarded"] == [
        ("calling-sid", "tool_output_delta", {"id": "R1", "text": "root "}),
        ("calling-sid", "tool_output_delta", {"id": "R1", "text": "= Stack()"}),
    ]
    assert (
        "generate_ui: using direct LLM stream protocol=openai_response "
        "model=gpt-5-codex-api-key tool_use_id=R1"
    ) in caplog.text
    assert (
        "generate_ui: direct LLM first_token raw_content='root ' "
        "protocol=openai_response model=gpt-5-codex-api-key tool_use_id=R1"
    ) in caplog.text
    assert (
        "generate_ui: direct LLM first_token text='root ' "
        "protocol=openai_response model=gpt-5-codex-api-key tool_use_id=R1"
    ) in caplog.text
    assert "generate_ui: direct LLM stream_chunk #" not in caplog.text
    assert (
        "generate_ui: direct LLM stream finished status=ok protocol=openai_response "
        "model=gpt-5-codex-api-key chunks=2 chars=14 tool_use_id=R1"
    ) in caplog.text


async def test_direct_llm_logs_finished_when_stream_errors(patched, monkeypatch, caplog):
    class _FailingChatModel:
        async def astream(self, messages):
            yield SimpleNamespace(content="partial")
            raise RuntimeError("stream broke")

    monkeypatch.setattr(
        r,
        "_build_direct_chat_model",
        lambda *, model, mp: _FailingChatModel(),
    )
    mp = SimpleNamespace(
        base_url="https://example.test/v1",
        api_key="k",
        api_protocol="anthropic",
    )
    completer = r._make_completer(
        user_id="u1",
        runtime_provider="codex",
        model="deepseek-v4-flash",
        mp=mp,
        calling_session_id="calling-sid",
        tool_use_id="R2",
    )

    with caplog.at_level(logging.INFO, logger=r.__name__):
        with pytest.raises(RuntimeError, match="stream broke"):
            await completer("PROMPT")

    assert (
        "generate_ui: direct LLM stream finished status=error protocol=anthropic "
        "model=deepseek-v4-flash chunks=1 chars=7 tool_use_id=R2"
    ) in caplog.text


async def test_direct_llm_falls_back_to_non_stream_when_stream_is_blank(
    patched, monkeypatch, caplog
):
    class _BlankStreamChatModel:
        async def astream(self, messages):
            patched["stream_messages"] = messages
            yield SimpleNamespace(content="")

        async def ainvoke(self, messages):
            patched["invoke_messages"] = messages
            return SimpleNamespace(content="Fallback Chart")

    monkeypatch.setattr(
        r,
        "_build_direct_chat_model",
        lambda *, model, mp: _BlankStreamChatModel(),
    )
    mp = SimpleNamespace(
        base_url="https://example.test/v1",
        api_key="k",
        api_protocol="anthropic",
    )
    completer = r._make_completer(
        user_id="u1",
        runtime_provider="codex",
        model="deepseek-v4-flash",
        mp=mp,
        calling_session_id="calling-sid",
        tool_use_id="R3",
    )

    with caplog.at_level(logging.INFO, logger=r.__name__):
        out = await completer("PROMPT")

    assert out == "Fallback Chart"
    assert patched["stream_messages"][0].content == "PROMPT"
    assert patched["invoke_messages"][0].content == "PROMPT"
    assert patched["forwarded"] == [
        ("calling-sid", "tool_output_delta", {"id": "R3", "text": "Fallback Chart"})
    ]
    assert (
        "generate_ui: direct LLM stream produced no text; trying non-stream fallback "
        "protocol=anthropic model=deepseek-v4-flash tool_use_id=R3"
    ) in caplog.text
    assert "generate_ui: direct LLM stream_chunk #" not in caplog.text
    assert (
        "generate_ui: direct LLM non-stream fallback text='Fallback Chart' "
        "protocol=anthropic model=deepseek-v4-flash tool_use_id=R3"
    ) in caplog.text
    assert (
        "generate_ui: direct LLM stream finished status=ok protocol=anthropic "
        "model=deepseek-v4-flash chunks=0 chars=14 tool_use_id=R3"
    ) in caplog.text
