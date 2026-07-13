"""genui prompt builder — pure function tests."""

from valuz_agent.modules.genui.prompts import (
    GENERATIVE_UI_INSTRUCTIONS,
    TOOL_DESCRIPTION,
    build_openui_prompt,
)


def test_build_prompt_splices_request_and_data():
    p = build_openui_prompt("a bar chart of Q1-Q4 sales", {"q1": 10, "q2": 20})
    assert "REQUEST:" in p
    assert "a bar chart of Q1-Q4 sales" in p
    assert '"q1": 10' in p
    # bundled library prompt is large
    assert len(p) > 500


def test_build_prompt_without_data():
    p = build_openui_prompt("just a table")
    assert "REQUEST:" in p
    assert "just a table" in p


def test_constants_are_set():
    assert "UI" in TOOL_DESCRIPTION and "chart" in TOOL_DESCRIPTION.lower()
    assert "OpenUI Lang" in GENERATIVE_UI_INSTRUCTIONS
