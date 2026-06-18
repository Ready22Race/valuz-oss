"""deepagents subagent recursion-limit shim.

Regression: deepagents gives the *main* graph ``recursion_limit=9_999`` but
subagents (incl. the auto-added ``general-purpose``) run at langgraph's default
of 25, dying with "Recursion limit of 25 reached" on any non-trivial subtask.
``_patches.apply_deepagents_patches`` bakes the higher limit into every compiled
subagent runnable.
"""

# ruff: noqa: I001
from __future__ import annotations

import valuz_agent.boot.kernel  # noqa: F401  (puts kernel `src` on the import path)
from langchain_core.runnables import RunnableLambda

from src.runtimes.deepagents._patches import (
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
