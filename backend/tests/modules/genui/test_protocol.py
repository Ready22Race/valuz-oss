"""A2UI prompt and payload tests.

A2UI v0.9 is the only wire protocol; the OpenUI Lang generation path was
removed rather than maintained alongside it.
"""

from __future__ import annotations

import json

from valuz_agent.modules.genui.prompts import TOOL_DESCRIPTION
from valuz_agent.modules.genui.protocol import (
    OUTPUT_FORMAT,
    a2ui_instructions,
    build_a2ui_prompt,
    wrap_generated_ui,
)


def test_tool_description_states_when_to_call_it():
    assert "UI" in TOOL_DESCRIPTION and "chart" in TOOL_DESCRIPTION.lower()


def test_prompt_splices_request_and_data():
    prompt = build_a2ui_prompt("a bar chart of Q1-Q4 sales", {"q1": 10})
    assert "REQUEST:" in prompt
    assert "a bar chart of Q1-Q4 sales" in prompt
    assert '"q1": 10' in prompt


def test_a2ui_prompt_describes_message_stream_and_openui_catalog():
    prompt = build_a2ui_prompt("sales dashboard", {"revenue": 12})

    assert "A2UI" in prompt
    assert "v0.9" in prompt
    assert "createSurface" in prompt
    assert "updateComponents" in prompt
    assert "OpenUI component catalog" in prompt
    assert "@a2ui/react" in prompt
    assert '"path":"/","value":{...}' in prompt
    assert '"text":"Revenue"' in prompt
    assert 'not nested under "props"' in prompt
    assert '"revenue": 12' in prompt
    assert "Valuz semantic components" in prompt
    assert "MarketIndexGrid" in prompt
    assert "MarketIndexCard" in prompt
    # FinanceMetric was retired in favour of the StatsCard block, which carries
    # the same label/value/delta/description shape. The name still resolves in
    # the renderer for older payloads, but the model is no longer taught it —
    # see test_a2ui_block_catalog.py.
    assert "StatsCard" in prompt
    assert "MarketBreadth" in prompt
    assert "DataList" in prompt
    # The row anatomy used to be spelled out in hand-written catalog prose.
    # It now comes from the DataList block's own description, which is
    # generated — so assert the substance rather than the retired wording.
    assert "leaderboards" in prompt
    assert "Do not create placeholder charts" in prompt


def test_session_instruction_and_output_format_name_the_stream():
    assert "A2UI" in a2ui_instructions()
    assert OUTPUT_FORMAT == "A2UI v0.9 JSON message stream"


def test_wrap_generated_ui_puts_the_stream_in_the_client_envelope():
    # The envelope is what the client dispatches on; a bare stream would reach
    # the renderer only by sniffing, which is what the envelope exists to avoid.
    wrapped = json.loads(
        wrap_generated_ui('{"version":"v0.9","createSurface":{"surfaceId":"s1"}}')
    )

    assert wrapped == {
        "protocol": "a2ui-json",
        "content": '{"version":"v0.9","createSurface":{"surfaceId":"s1"}}',
    }
