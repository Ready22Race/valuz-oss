"""project_instructions tool — validation + project-only gating.

The happy-path DB write delegates to ``ProjectService.update_instructions``
(covered by the projects service tests); here we pin the tool's own logic: arg
validation and the gate that makes it usable ONLY inside a project session.
"""

# ruff: noqa: I001  (kernel bootstrap must import before src.core)
from __future__ import annotations

import asyncio

import valuz_agent.boot.kernel  # noqa: F401  (sets kernel import path)
from src.core.tools import ExecContext

import valuz_agent.modules.projects.tools as t


def _const(value):  # noqa: ANN001, ANN202 — async stub factory for monkeypatch
    async def _f(*_a, **_k):  # noqa: ANN002, ANN003, ANN202
        return value

    return _f


def test_rejects_bad_action_and_missing_content() -> None:
    ctx = ExecContext(session_id="proj")
    assert asyncio.run(
        t._handler({"action": "frob", "content": "x"}, ctx)
    ).is_error
    assert asyncio.run(t._handler({"action": "set", "content": ""}, ctx)).is_error
    assert asyncio.run(t._handler({"action": "set"}, ctx)).is_error


def test_gated_to_project_sessions(monkeypatch) -> None:  # noqa: ANN001
    # No project on the session → refused (this is the "project only" gate).
    monkeypatch.setattr(t, "_resolve_project_id", _const(None))
    r = asyncio.run(
        t._handler({"action": "set", "content": "be concise"}, ExecContext(session_id="chat"))
    )
    assert r.is_error
    assert "no project" in r.content


def test_tool_def_shape() -> None:
    (td,) = t.build_project_instructions_tool_defs()
    assert td.name == t.PROJECT_INSTRUCTIONS_TOOL_NAME == "project_instructions"
    # only ``action`` is required — ``content`` is omitted for get.
    assert td.parameters["required"] == ["action"]
    assert set(td.parameters["properties"]["action"]["enum"]) == {"get", "set", "append"}
    assert td.read_only is False


def test_get_needs_no_content_but_still_gated(monkeypatch) -> None:  # noqa: ANN001
    # get is valid without content, but still refused outside a project.
    monkeypatch.setattr(t, "_resolve_project_id", _const(None))
    r = asyncio.run(t._handler({"action": "get"}, ExecContext(session_id="chat")))
    assert r.is_error
    assert "no project" in r.content
