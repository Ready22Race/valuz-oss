"""Failure — the module's typed "this did not work, here is why".

Pure domain, no IO. Exists so a function that can fail without raising says so
in its TYPE rather than by returning a value the caller has to sniff.

The idiom it replaces was ``T | str``::

    def check_lead_gate(sess) -> tuple[str, str] | str: ...

    verdict = check_lead_gate(sess)
    if isinstance(verdict, str):        # ← failure, apparently
        ...

Three things are wrong with that. Success and failure are told apart by their
RUNTIME TYPE, so it collapses the moment a function legitimately wants to
return a string. A forgotten ``isinstance`` check is not a type error, so the
error message flows onward as if it were the result. And three unrelated layers
(``resolution`` / ``planning`` / ``tools.gate``) each reinvented it
independently.

``T | Failure`` fixes all three at no runtime cost: ``Failure.reason`` carries
exactly the string the old form did, so nothing on the wire changes.

Scope — this is NOT the module's single error convention, and deliberately so:

* Service functions that back an MCP tool (``plan_task``, ``dispatch_async``,
  ``await_member_results``, …) return ``{"error": ..., "hint": ...,
  "ready_keys": ...}`` dicts. Those are not exception substitutes, they are the
  tool's WIRE PAYLOAD — structured guidance the model reads and acts on. Their
  shape is a contract with the agent, not an internal convention to tidy.
* Genuinely exceptional conditions (an invalid plan mutation, an illegal task
  status transition) still raise ``PlanError`` / ``TaskStateError``.

So: raise for programmer errors, ``Failure`` for expected in-process failures,
error dicts where the dict IS the answer being sent somewhere.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Failure:
    """An expected failure with a human-readable reason.

    ``reason`` is surfaced verbatim — to the model as a tool error, to the user
    as an HTTP detail, or into a task event payload — so write it for whoever
    ends up reading it, not for a log line.
    """

    reason: str

    def __str__(self) -> str:  # so f"{failure}" reads naturally at call sites
        return self.reason


__all__ = ["Failure"]
