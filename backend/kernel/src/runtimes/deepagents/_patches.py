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

# deepagents gives the *main* graph a huge budget — ``deepagents.graph`` applies
# ``.with_config({"recursion_limit": 9_999})`` to the top-level deep agent — but
# **subagents never inherit it**: ``SubAgentMiddleware`` compiles each subagent
# with a bare ``create_agent(...)`` (no ``.with_config``), and the
# ``task``/``atask`` tools rebuild the per-invocation config with only
# ``configurable`` — dropping the parent's top-level ``recursion_limit``. So
# every subagent (including the auto-added ``general-purpose`` one) runs at
# langgraph's default of 25 and dies with "Recursion limit of 25 reached
# without hitting a stop condition" on any non-trivial subtask.
#
# We bake a higher limit into each compiled subagent runnable at the single
# chokepoint where they are finalized — but a *subtask*-sized one, not the
# orchestrator's 9_999. A subagent handles one focused subtask; ~200 supersteps
# (≈100 model/tool turns) is a generous ceiling that still fails fast on a
# runaway loop instead of burning thousands of LLM calls. Tune here if a
# legitimate subtask ever needs more headroom.
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
