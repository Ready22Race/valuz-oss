"""Memory P1: background extractor core (prompt-agnostic) tests."""

# ruff: noqa: I001  (kernel_bootstrap must import before src.core)
from __future__ import annotations

import asyncio
import json

import pytest

import valuz_agent.boot.kernel  # noqa: F401  (sets kernel import path)
from valuz_agent.modules.memory import MemoryStore
from valuz_agent.modules.memory.extraction import (
    MemoryExtractor,
    apply_ops,
    parse_ops,
    redact_secrets,
)


def _completer(payload):  # noqa: ANN001, ANN202 — async stub returning a fixed body
    async def _c(_prompt):  # noqa: ANN001, ANN202
        return payload

    return _c


@pytest.fixture
def store(tmp_path, monkeypatch):  # noqa: ANN001, ANN201
    from valuz_agent.infra import fs_registry as fsmod

    monkeypatch.setattr(fsmod.FsRegistry, "data_dir", lambda self: tmp_path / "app")
    return MemoryStore()


def test_redact_secrets():
    out = redact_secrets("key sk-ABCDEFGH012345678 and Authorization: Bearer abcdef123456")
    assert "sk-ABCDEFGH" not in out and "Bearer abcdef" not in out
    assert out.count("[REDACTED_SECRET]") >= 2
    assert "[REDACTED_SECRET]" in redact_secrets("api_key=supersecretvalue")


def test_parse_ops_plain_and_fenced():
    body = {"ops": [{"action": "add", "target": "user", "content": "be terse"}], "note": "x"}
    assert parse_ops(json.dumps(body))[0].content == "be terse"
    fenced = "```json\n" + json.dumps(body) + "\n```"
    assert parse_ops(fenced)[0].target == "user"
    prosed = "Sure, here you go:\n" + json.dumps(body) + "\nDone."
    assert len(parse_ops(prosed)) == 1


def test_parse_ops_drops_malformed():
    raw = json.dumps(
        {
            "ops": [
                {"action": "add", "target": "user", "content": "ok"},
                {"action": "add", "target": "user"},  # missing content
                {"action": "replace", "target": "global", "old_text": "x"},  # missing content
                {"action": "frob", "target": "user", "content": "y"},  # bad action
                {"action": "add", "target": "nope", "content": "y"},  # bad target
            ]
        }
    )
    ops = parse_ops(raw)
    assert len(ops) == 1 and ops[0].content == "ok"
    assert parse_ops("not json at all") == []


def test_apply_ops_scope_routing(store):
    ops = parse_ops(
        json.dumps(
            {
                "ops": [
                    {"action": "add", "target": "user", "content": "be terse"},
                    {"action": "add", "target": "global", "content": "prefers pnpm"},
                    {"action": "add", "target": "project", "content": "tracks ACME"},
                ]
            }
        )
    )
    # no project bound -> the project op is skipped, user/global applied
    rep = apply_ops(ops, project_id=None, store=store)
    assert rep["applied"] == 2 and rep["skipped"]
    assert store.read_entries("user") == ["be terse"]
    assert store.read_entries("global") == ["prefers pnpm"]
    # with a project -> project op lands
    rep2 = apply_ops(ops, project_id="p1", store=store)
    assert "tracks ACME" in store.read_entries("project", project_id="p1")
    assert rep2["applied"] >= 1


def test_apply_ops_redacts_before_persist(store):
    ops = parse_ops(
        json.dumps(
            {
                "ops": [
                    {"action": "add", "target": "global", "content": "token=sk-ABCDEFGH012345678"}
                ]
            }
        )
    )
    apply_ops(ops, store=store)
    stored = store.read_entries("global")[0]
    assert "sk-ABCDEFGH" not in stored and "[REDACTED_SECRET]" in stored


def test_extractor_end_to_end(store):
    payload = json.dumps(
        {"ops": [{"action": "add", "target": "user", "content": "investor, replies in zh"}]}
    )
    ext = MemoryExtractor(store=store, complete=_completer(payload))
    assert ext.enabled
    rep = asyncio.run(ext.extract(transcript="user: I'm an investor; reply in Chinese"))
    assert rep["applied"] == 1
    assert "investor, replies in zh" in store.read_entries("user")


def test_extractor_inert_without_completer(store):
    ext = MemoryExtractor(store=store)
    assert not ext.enabled
    rep = asyncio.run(ext.extract(transcript="anything"))
    assert rep["applied"] == 0 and "skipped" in rep
