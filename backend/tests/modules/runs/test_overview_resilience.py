"""A single malformed session must not blank the whole activity overview.

``list_runs`` enriches each kernel session into a ``RunSummary``. If one
session's enrichment raises (a bad todo, a missing relation, …) the overview
must skip that session and still return the rest — historically an unguarded
failure here made the entire Activity view disappear.
"""

# ruff: noqa: I001
from __future__ import annotations

from types import SimpleNamespace

import pytest

import valuz_agent.boot.kernel  # noqa: F401  (puts kernel on the import path)
from valuz_agent.modules.runs import service as svc_mod
from valuz_agent.modules.runs.service import RunsService


class _FakeStore:
    async def list_projects(self, *_a, **_k):
        return []

    async def list_all(self, *_a, **_k):
        return []

    async def list_run_session_ids(self, *_a, **_k):
        return set()


@pytest.mark.asyncio
async def test_list_runs_skips_a_session_that_fails_to_build(monkeypatch):
    sessions = [
        SimpleNamespace(id="bad", status="running", created_at=1),
        SimpleNamespace(id="ok", status="running", created_at=2),
    ]

    monkeypatch.setattr(svc_mod, "require_current_user_id", lambda: "u1")
    monkeypatch.setattr(
        svc_mod.project_index,
        "list_recent",
        _async_return(
            [SimpleNamespace(session_id=s.id, project_id="") for s in sessions]
        ),
    )
    monkeypatch.setattr(
        svc_mod.kernel_client, "list_sessions", _async_return(sessions)
    )

    store = _FakeStore()
    service = RunsService(store, store, store, store, store)

    async def _fake_build(sess, *_a, **_k):
        if sess.id == "bad":
            raise ValueError("boom — malformed session")
        return SimpleNamespace(session_id=sess.id, updated_at=sess.created_at)

    service._build = _fake_build  # type: ignore[method-assign]

    out = await service.list_runs(status="running")

    assert [r.session_id for r in out] == ["ok"]


def _async_return(value):
    async def _inner(*_a, **_k):
        return value

    return _inner
