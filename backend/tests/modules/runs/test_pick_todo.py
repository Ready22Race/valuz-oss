"""``_pick_todo`` selects the most relevant TODO for the activity overview.

Regression: ``todos`` arrive as kernel ``TodoItem`` models (pydantic), not
dicts. An earlier version called ``.get`` on them and blew up with
``'TodoItem' object has no attribute 'get'`` while listing runs.
"""

# ruff: noqa: I001
from __future__ import annotations

import valuz_agent.boot.kernel  # noqa: F401  (sets kernel import path)
from app.schemas import TodoItem
from valuz_agent.modules.runs.service import _pick_todo


def _todo(content: str, status: str, active_form: str | None = None) -> TodoItem:
    return TodoItem(content=content, status=status, activeForm=active_form)


def test_none_and_empty():
    assert _pick_todo(None) is None
    assert _pick_todo([]) is None


def test_prefers_in_progress():
    snap = _pick_todo(
        [
            _todo("first", "pending"),
            _todo("doing it", "in_progress", "Doing it"),
            _todo("later", "pending"),
        ]
    )
    assert snap is not None
    assert snap.content == "doing it"
    assert snap.status == "in_progress"
    assert snap.activeForm == "Doing it"


def test_falls_back_to_first_pending():
    snap = _pick_todo([_todo("done", "completed"), _todo("next", "pending")])
    assert snap is not None
    assert snap.content == "next"
    assert snap.status == "pending"


def test_falls_back_to_last_when_nothing_active():
    snap = _pick_todo([_todo("a", "completed"), _todo("b", "completed")])
    assert snap is not None
    assert snap.content == "b"


def test_none_when_chosen_has_no_content():
    assert _pick_todo([_todo("", "in_progress")]) is None
