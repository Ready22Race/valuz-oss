"""Member-run state classification — pure domain, no IO.

How to read a dispatched member's kernel session state and decide what the
host should do about it. Sits beside ``plan.py`` and ``task_state.py`` as the
module's third pure vocabulary: no DB, no kernel client, no service instance,
so every rule here is unit-testable on plain values.

It lived inside ``recovery.py`` until 2026-07. Two services need it —
``RecoveryService`` (startup + resume sweeps) and
``CoordinationService._heartbeat_pending`` (the online backstop for a member
whose ``member_done`` never arrived) — and since recovery already imports
coordination for the shutdown broadcast, coordination could only reach back for
``classify_member`` through a function-local import. That is a real circular
dependency, merely hidden. Pure rules do not belong to either service; giving
them their own home removes the cycle instead of masking it.

The side effects the classification implies (DB writes, respawning an actor
loop, delivering ``member_done``) stay in ``RecoveryService`` — this module
only says WHAT is true, never DOES anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

# resume     — re-run the member (kernel run_turn on the persisted session);
#              covers "created but never ran" and "interrupted by host_restart".
# completed  — member reached a normal terminal turn (end_turn); collect + review.
# failed     — member errored terminally (non-restart), or resume retry exhausted.
# in_flight  — member is genuinely still running; leave it to the heartbeat/mailbox.
Disposition = Literal["resume", "completed", "failed", "in_flight"]

# Resume retry cap (VALUZ-RESUME §5.0): a member node may be resumed at most this
# many times before we give up and hand it back to the lead as rework.
RESUME_RETRY_CAP = 3


def _stop_reason_dict(stop_reason: Any) -> dict[str, Any]:
    """Normalise a kernel ``stop_reason`` (dict or Error-like object) to a dict."""
    if not stop_reason:
        return {}
    if isinstance(stop_reason, dict):
        return stop_reason
    return {
        "type": getattr(stop_reason, "type", None),
        "category": getattr(stop_reason, "category", None),
        "message": getattr(stop_reason, "message", None),
    }


def classify_member(status: str | None, stop_reason: Any) -> Disposition:
    """Classify a member subtask from its kernel session state.

    ``status``/``stop_reason`` are the kernel ``Session`` fields (status is None
    when the session row is missing entirely).
    """
    if status is None or status == "created":
        return "resume"  # built but never ran (app stopped before the first turn)
    if status == "running":
        return "in_flight"  # genuinely active — don't touch
    sr = _stop_reason_dict(stop_reason)
    typ = sr.get("type")
    if typ == "end_turn":
        return "completed"  # normal terminal turn
    if typ == "error":
        # Interrupted mid-flight → resumable. Three ways a turn loses its
        # process without it being a task failure:
        #   * host_restart — a hard kill left the row ``running``; boot
        #     recovery flipped it (host liveness-aware reset, or the kernel's
        #     own scan on the ``local`` tier).
        #   * interrupted — a graceful host stop tore down the runtime
        #     subprocess and the runtime stamped it resumable itself.
        #   * user_interrupt — the user cancelled the in-flight turn (every
        #     runtime stamps this category on an explicit interrupt). A user
        #     pressing stop is intent, not a failure.
        # Any other error = a real execution failure.
        return (
            "resume"
            if sr.get("category") in ("host_restart", "interrupted", "user_interrupt")
            else "failed"
        )
    # idle with no / unknown stop_reason → conservatively resumable.
    return "resume"


@dataclass(frozen=True)
class MemberReconcile:
    """The host-side plan for one member run, derived purely from its state.

    The orchestrator applies it: write ``run_status`` to the
    ``valuz_task_session`` row and ``node_status`` to the plan node; if
    ``resume`` respawn the member actor loop (kernel run_turn); if
    ``deliver_member_done`` put a member_done into the lead's mailbox.
    """

    disposition: Disposition
    run_status: str | None  # new valuz_task_session.status (None = leave as-is)
    node_status: str | None  # new plan-node status (None = leave as-is)
    resume: bool  # caller should respawn the member actor loop
    deliver_member_done: bool  # caller should notify the lead via mailbox
    reason: str = ""


def reconcile(
    status: str | None,
    stop_reason: Any,
    *,
    node_attempts: int,
    retry_cap: int = RESUME_RETRY_CAP,
) -> MemberReconcile:
    """Map a member's kernel state + retry count to a concrete disposition.

    Pure — no I/O. ``node_attempts`` is the plan node's ``attempts`` (resume
    count); once it reaches ``retry_cap`` a would-be resume becomes a failure
    so a broken member can't be respawned forever.
    """
    disp = classify_member(status, stop_reason)
    if disp == "in_flight":
        return MemberReconcile("in_flight", None, None, False, False)
    if disp == "completed":
        return MemberReconcile("completed", "completed", "in_review", False, True)
    if disp == "failed":
        msg = _stop_reason_dict(stop_reason).get("message") or "member session errored"
        return MemberReconcile("failed", "archived", "rework", False, False, reason=str(msg))
    # disp == "resume"
    if node_attempts >= retry_cap:
        return MemberReconcile(
            "failed",
            "archived",
            "rework",
            False,
            False,
            reason=f"resume retry cap ({retry_cap}) exhausted",
        )
    return MemberReconcile("resume", "active", "in_progress", True, False)
