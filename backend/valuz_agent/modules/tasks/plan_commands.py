"""PlanCommandService — the single authorized entry point for plan writes.

A task's plan has two transports: the MCP tools an agent calls, and the REST
routes a human client calls. They had drifted into two different answers to
"who may change this plan":

  * the tool path resolved the caller's session and ran
    ``gate.check_plan_writer_gate`` (draft → originator or a project mate;
    active → the lead session only; anything else → read-only);
  * the REST path checked that the task belonged to the requesting user and
    called ``planning`` directly.

``planning.plan_task`` / ``modify_plan`` carry no status guard of their own —
only "a plan with progress already exists" and the CAS token — so over REST the
plan of a *completed* or *paused* task could still be rewritten, and an
*active* task's plan could be rewritten by someone who is not its lead. The
rules existed; one of the two doors just did not consult them.

This module is that door. Every plan write goes through it, and the order is
fixed: load the task, authorize the caller, check the status, check the CAS
token, mutate, emit. ``planning`` keeps the mutation itself and is no longer
called directly by either transport.

Two kinds of caller, deliberately not one
-----------------------------------------
The obvious unification — "take a ``caller_session_id`` and run the gate" —
is wrong, and it is worth saying why, because it is what the two transports had
been half-doing.

An AGENT's authority comes from its session's ROLE: this session is the lead of
this task, or it is a chat session in the task's project. That is what
``gate.py`` encodes. A HUMAN's authority comes from OWNING the task; a person
is not a lead session and never will be, so running the agent gate against them
would reject every human edit on an active task, and inventing a session id to
get past it would be authorization theatre.

Worse, over REST the session id arrives in the REQUEST BODY
(``PlanTaskRequest.lead_session_id``) — self-declared. Gating on a value the
caller supplies is not a check. :class:`AgentCaller` is therefore only ever
constructed from a session the transport itself resolved.

So the two are modelled separately and each is checked on its own terms.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from valuz_agent.adapters.data_reader import data_reader
from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.tasks import planning
from valuz_agent.modules.tasks.datastore import TaskDatastore
from valuz_agent.modules.tasks.models import TaskRow
from valuz_agent.modules.tasks.outcome import Failure
from valuz_agent.modules.tasks.plan import TaskPlan
from valuz_agent.modules.tasks.tools import gate

# Statuses whose plan may still change. Everything else — stopped, completed,
# blocked, abandoned — is a record of what happened and is read-only. This is
# the rule ``gate.check_plan_writer_gate`` already applied on the tool path;
# the REST path had no equivalent.
_WRITABLE_STATUSES = frozenset({"draft", "active"})


@dataclass(frozen=True)
class AgentCaller:
    """An agent session calling through the MCP tool surface.

    ``session_id`` MUST come from the tool-execution context, never from
    caller-supplied arguments — its whole purpose is to establish a role the
    caller cannot claim for itself.
    """

    session_id: str
    user_id: str


@dataclass(frozen=True)
class OwnerCaller:
    """A human client calling over HTTP, authorized by owning the task."""

    user_id: str


PlanCaller = AgentCaller | OwnerCaller


async def _load_task(caller: PlanCaller, task_id: str) -> TaskRow | Failure:
    """The caller's task, or a Failure. Owner scoping is the first gate for
    BOTH caller kinds: ``get_task`` filters on ``user_id``, so a task belonging
    to somebody else is indistinguishable from one that does not exist."""
    async with async_unit_of_work(commit=False) as db:
        task = await TaskDatastore(db).get_task(caller.user_id, task_id)
    if task is None:
        return Failure(f"task {task_id!r} not found")
    return task


async def _authorize_write(caller: PlanCaller, task: TaskRow) -> Failure | None:
    """May *caller* change this plan right now? ``None`` = yes."""
    if task.status not in _WRITABLE_STATUSES:
        return Failure(
            f"task {task.id!r} is {task.status!r}; its plan is a record of what "
            "happened and is read-only. Resume the task first if you need to "
            "change what it will do."
        )
    if isinstance(caller, OwnerCaller):
        # Owning the task is the whole check. A human is not a lead session, so
        # the role half of the agent gate cannot apply to them; what protects a
        # running task from a mid-air human edit is the CAS token, enforced by
        # ``modify_plan`` below.
        return None

    sess = await data_reader().get_session(caller.user_id, caller.session_id)
    if sess is None:
        return Failure("plan: caller session not found")
    return gate.check_plan_writer_gate(sess, task)


async def _authorize_read(caller: PlanCaller, task: TaskRow) -> Failure | None:
    """May *caller* read this plan? ``None`` = yes.

    Looser than writing on purpose: any caller in the task's project may look.
    For an owner, having loaded the task at all is already the answer.
    """
    if isinstance(caller, OwnerCaller):
        return None
    sess = await data_reader().get_session(caller.user_id, caller.session_id)
    if sess is None:
        return Failure("plan: caller session not found")
    return gate.check_plan_reader_gate(sess, task)


def _actor_of(caller: PlanCaller) -> str:
    """Who to record on the emitted events."""
    return caller.session_id if isinstance(caller, AgentCaller) else "user"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


async def plan_task(
    caller: PlanCaller, *, task_id: str, subtasks: list[dict[str, Any]]
) -> dict[str, Any]:
    """Lay down the initial plan. Authorized, then delegated to ``planning``."""
    task = await _load_task(caller, task_id)
    if isinstance(task, Failure):
        return {"error": task.reason}
    denied = await _authorize_write(caller, task)
    if denied is not None:
        return {"error": denied.reason}
    # A committed task's plan is laid down; changing it is ``modify_plan``'s
    # job, which bumps the CAS token and emits ``plan_revised`` rather than
    # ``task_planned``. This guard used to live in the MCP handler only, so the
    # REST path could re-plan a running task from scratch.
    if (task.committed_at is not None or task.status == "active") and not TaskPlan.from_dict(
        task.plan
    ).is_empty:
        return {
            "error": (
                "plan_task: this task already has a committed plan — use "
                "modify_plan to change it"
            )
        }
    return await planning.plan_task(
        task_id=task_id,
        project_id=task.project_id,
        lead_session_id=_actor_of(caller),
        subtasks=subtasks,
        user_id=caller.user_id,
    )


async def modify_plan(
    caller: PlanCaller,
    *,
    task_id: str,
    add: list[dict[str, Any]] | None = None,
    update: list[dict[str, Any]] | None = None,
    expected_version: int | None = None,
) -> dict[str, Any]:
    """Change a plan that already exists.

    ``expected_version`` is REQUIRED of an :class:`OwnerCaller` editing an
    ``active`` task: the lead is writing the same document concurrently, and
    without the CAS token a human edit silently overwrites whatever the lead
    did between the read and the write. An agent lead is the single writer on
    its own task and may omit it.
    """
    task = await _load_task(caller, task_id)
    if isinstance(task, Failure):
        return {"error": task.reason}
    denied = await _authorize_write(caller, task)
    if denied is not None:
        return {"error": denied.reason}
    if (
        isinstance(caller, OwnerCaller)
        and task.status == "active"
        and expected_version is None
    ):
        return {
            "error": "expected_version is required to edit a running task's plan",
            "current_version": task.plan_version or 0,
            "hint": (
                "The lead is writing this plan too. Read it with GET /plan, "
                "then send the version you saw so a concurrent lead edit is "
                "detected instead of silently overwritten."
            ),
        }
    return await planning.modify_plan(
        task_id=task_id,
        project_id=task.project_id,
        lead_session_id=_actor_of(caller),
        add=add,
        update=update,
        expected_version=expected_version,
        user_id=caller.user_id,
    )


async def get_plan(caller: PlanCaller, *, task_id: str) -> dict[str, Any]:
    """Read the plan snapshot + ready keys + counts."""
    task = await _load_task(caller, task_id)
    if isinstance(task, Failure):
        return {"error": task.reason}
    denied = await _authorize_read(caller, task)
    if denied is not None:
        return {"error": denied.reason}
    return await planning.get_plan(
        task_id=task_id, project_id=task.project_id, user_id=caller.user_id
    )


__all__ = [
    "AgentCaller",
    "OwnerCaller",
    "PlanCaller",
    "get_plan",
    "modify_plan",
    "plan_task",
]
