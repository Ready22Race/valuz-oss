"""Runtime compatibility patches for the third-party ``deepagents`` package.

These adjust upstream behavior we cannot reach through the public API.
``deepagents`` ships in the virtualenv (not vendored/editable), so we shim it
at import time. Every patch here is **idempotent** and **fails soft**: if a
future ``deepagents`` changes shape, we log and leave the original behavior in
place rather than crash the runtime at import.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# ── recursion limits ────────────────────────────────────────────────────────
# langgraph defaults every graph to a 25-superstep recursion limit; a non-trivial
# agent turn (each model→tool round is ≥2 supersteps, more through deepagents'
# middleware stack) blows past 25 and dies with "Recursion limit of 25 reached
# without hitting a stop condition". deepagents tries to avoid this by binding a
# huge budget to the *main* graph — ``deepagents.graph`` does
# ``create_agent(...).with_config({"recursion_limit": 9_999})`` — but that budget
# fails to reach execution on BOTH the paths this runtime uses, for two distinct
# reasons. These two constants are the fixes; each is applied at its own seam.
#
# 1) MAIN GRAPH — invoked by the runtime via ``graph.astream_events(...)``.
#    ``astream_events`` does NOT honor a ``RunnableBinding``'s bound
#    ``recursion_limit`` (``.astream``/``.ainvoke`` do, but ``astream_events``
#    drops it — verified empirically against langchain-core 1.3.2 / langgraph),
#    so deepagents' bound 9_999 is silently ignored and the main loop runs at the
#    default 25. The bound value is unreachable from here, so we pass the limit
#    EXPLICITLY in the call-time ``stream_config`` instead (call-time config IS
#    honored by ``astream_events``). See ``runtime.py``'s stream config. The
#    limit is enforced per ``astream_events`` call (≈ per turn / per HITL-resume
#    segment), not cumulatively across the thread — so this is a generous
#    per-turn ceiling: ~1000 supersteps (≈500 model/tool rounds) never bites a
#    legitimate deep turn while still bounding a runaway loop. Lower than
#    deepagents' effectively-unbounded 9_999 so a true loop fails in minutes, not
#    thousands of LLM calls.
MAIN_GRAPH_RECURSION_LIMIT = 1_000
#
# 2) SUBAGENTS — invoked via ``task``/``atask`` → ``subagent.invoke(...)`` /
#    ``ainvoke(...)`` (which DO honor a bound ``recursion_limit``). But subagents
#    never inherit the main graph's budget: ``SubAgentMiddleware`` compiles each
#    one with a bare ``create_agent(...)`` (no ``.with_config``), and ``task``
#    rebuilds the per-invocation config with only ``configurable`` (deepagents
#    deliberately keeps it minimal "so this will [not] block out manual
#    ``.with_config``"). So every subagent — including the auto-added
#    ``general-purpose`` one — runs at the default 25. Since the invoke path
#    honors bound config, we bake the limit into each compiled subagent runnable
#    at the single chokepoint where they are finalized (``_get_subagents``). A
#    subagent handles one focused subtask, so this is a *subtask*-sized ceiling,
#    not the orchestrator's: ~200 supersteps (≈100 model/tool rounds). Tune here
#    if a legitimate subtask ever needs more headroom.
SUBAGENT_RECURSION_LIMIT = 200


def _patch_subagent_recursion_limit() -> None:
    """Give deepagents subagents the same recursion budget as the main graph."""
    try:
        from deepagents.middleware.subagents import SubAgentMiddleware
    except Exception:  # pragma: no cover - upstream layout changed
        logger.warning(
            "deepagents SubAgentMiddleware import failed; "
            "subagent recursion-limit patch skipped"
        )
        return

    original = getattr(SubAgentMiddleware, "_get_subagents", None)
    if original is None:  # pragma: no cover - upstream renamed the method
        logger.warning(
            "deepagents SubAgentMiddleware._get_subagents missing; "
            "subagent recursion-limit patch skipped"
        )
        return
    if getattr(original, "_valuz_recursion_patched", False):
        return

    def _get_subagents_with_recursion_limit(self):  # type: ignore[no-untyped-def]
        specs = original(self)
        for spec in specs:
            runnable = spec.get("runnable")
            if runnable is not None and hasattr(runnable, "with_config"):
                spec["runnable"] = runnable.with_config(
                    {"recursion_limit": SUBAGENT_RECURSION_LIMIT}
                )
        return specs

    _get_subagents_with_recursion_limit._valuz_recursion_patched = True  # type: ignore[attr-defined]
    SubAgentMiddleware._get_subagents = _get_subagents_with_recursion_limit  # type: ignore[method-assign]


def apply_deepagents_patches() -> None:
    """Apply all deepagents compatibility patches. Safe to call repeatedly."""
    _patch_subagent_recursion_limit()
