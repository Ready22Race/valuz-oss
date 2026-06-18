"""deepagents recursion-limit fixes — both the subagent and main-graph paths.

langgraph defaults every graph to a 25-superstep recursion limit, which any
non-trivial agent turn blows past ("Recursion limit of 25 reached"). deepagents
binds ``recursion_limit=9_999`` onto the main graph to dodge this, but that
budget never reaches execution on either path this runtime uses:

* **Subagents** are invoked via ``invoke``/``ainvoke`` (which DO honor a bound
  budget) but are compiled without one — so they run at 25.
  ``_patches.apply_deepagents_patches`` bakes ``SUBAGENT_RECURSION_LIMIT`` into
  every compiled subagent runnable.
* **The main graph** is invoked via ``astream_events``, which *drops* the bound
  budget (verified below). The runtime passes ``MAIN_GRAPH_RECURSION_LIMIT`` in
  the call-time config instead — the path ``astream_events`` does honor.

The ``astream_events`` tests are canaries: if a langchain-core upgrade changes
either behavior, they fail and tell us the workaround can be revisited.
"""

# ruff: noqa: I001
from __future__ import annotations

from typing import TypedDict

import pytest

import valuz_agent.boot.kernel  # noqa: F401  (puts kernel `src` on the import path)
from langchain_core.runnables import RunnableLambda
from langgraph.errors import GraphRecursionError
from langgraph.graph import START, StateGraph

from src.runtimes.deepagents._patches import (
    MAIN_GRAPH_RECURSION_LIMIT,
    SUBAGENT_RECURSION_LIMIT,
    apply_deepagents_patches,
)


def _middleware_with_stub_subagent():
    from deepagents.middleware.subagents import SubAgentMiddleware

    stub = RunnableLambda(lambda x: x)
    # CompiledSubAgent path: the runnable is used as-is, so the only thing that
    # can lift its recursion limit is our patch.
    return SubAgentMiddleware(
        backend=object(),
        subagents=[{"name": "t", "description": "d", "runnable": stub}],
    )


def test_subagent_runnable_gets_main_graph_recursion_limit():
    apply_deepagents_patches()
    mw = _middleware_with_stub_subagent()
    spec = mw._get_subagents()[0]
    assert spec["runnable"].config.get("recursion_limit") == SUBAGENT_RECURSION_LIMIT


def test_patch_is_idempotent():
    from deepagents.middleware.subagents import SubAgentMiddleware

    apply_deepagents_patches()
    first = SubAgentMiddleware._get_subagents
    apply_deepagents_patches()
    assert SubAgentMiddleware._get_subagents is first
    assert getattr(first, "_valuz_recursion_patched", False) is True


# --- main-graph path: ``astream_events`` recursion-limit behavior ------------
#
# These pin the langchain-core/langgraph behavior the runtime's main-graph fix
# depends on. A tiny graph that loops forever lets us observe exactly which limit
# the recursion guard enforces.

DEFAULT_LANGGRAPH_RECURSION_LIMIT = 25


class _Counter(TypedDict):
    n: int


def _forever_looping_graph():
    """A graph whose only node loops back to itself — never terminates, so the
    recursion limit is the only thing that stops it."""

    def step(state: _Counter) -> _Counter:
        return {"n": state["n"] + 1}

    g = StateGraph(_Counter)
    g.add_node("step", step)
    g.add_edge(START, "step")
    g.add_edge("step", "step")
    return g.compile()


async def _limit_hit_via_astream_events(runnable, config) -> int:
    """Run ``runnable`` via ``astream_events`` until the recursion guard fires;
    return the limit reported in the error message."""
    with pytest.raises(GraphRecursionError) as excinfo:
        async for _ in runnable.astream_events({"n": 0}, config, version="v2"):
            pass
    # message: "Recursion limit of N reached without hitting a stop condition."
    return int(str(excinfo.value).split("Recursion limit of ", 1)[1].split(" ", 1)[0])


@pytest.mark.asyncio
async def test_astream_events_honors_call_time_recursion_limit():
    """The runtime's fix: a ``recursion_limit`` in the call-time config IS
    honored by ``astream_events``."""
    graph = _forever_looping_graph()
    hit = await _limit_hit_via_astream_events(
        graph, {"configurable": {}, "recursion_limit": 8}
    )
    assert hit == 8


@pytest.mark.asyncio
async def test_astream_events_drops_bound_recursion_limit():
    """Canary for the upstream quirk that motivates the fix: a budget *bound*
    onto the graph with ``.with_config`` (as deepagents does, 9_999) is dropped
    by ``astream_events`` — it falls back to langgraph's default of 25. If this
    ever starts passing, ``astream_events`` was fixed and deepagents' bound 9_999
    now applies on its own.
    """
    bound = _forever_looping_graph().with_config({"recursion_limit": 9_999})
    hit = await _limit_hit_via_astream_events(bound, {"configurable": {}})
    assert hit == DEFAULT_LANGGRAPH_RECURSION_LIMIT


def test_main_graph_limit_is_a_generous_per_turn_ceiling():
    """Sanity: the main-graph budget clears the default and sits above the
    subtask-sized subagent ceiling."""
    assert MAIN_GRAPH_RECURSION_LIMIT > DEFAULT_LANGGRAPH_RECURSION_LIMIT
    assert MAIN_GRAPH_RECURSION_LIMIT >= SUBAGENT_RECURSION_LIMIT
