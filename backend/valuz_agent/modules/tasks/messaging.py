"""Lead ↔ member / chat → task text delivery (mailbox-backed).

Extracted from ``TaskOrchestrator`` (T1.1 split). Both functions are stateless
— they validate against the DB and post to the global ``mailbox_registry`` —
so they live as module functions. The orchestrator keeps thin delegators as the
coordinator surface the dispatch-MCP tools + task routes drive.
"""

from __future__ import annotations

from typing import Any

from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.tasks.datastore import (
    TaskDatastore,
    TaskEventDatastore,
    TaskSessionDatastore,
)


async def send_to_member(
    *,
    from_session_id: str,
    to_session_id: str,
    text: str,
    project_id: str,
    task_id: str,
    user_id: str,
) -> dict[str, Any]:
    """Deliver a free-text follow-up from the lead to a running member.

    Task-level isolation (dual isolation): the target must be a member of
    the **caller lead's task**. The mailbox is a global per-session
    registry, so without this check a lead could (if it ever obtained a
    sibling task's session id) deliver across tasks. We refuse any target
    whose run doesn't belong to ``task_id``.
    """
    from valuz_agent.modules.tasks.mailbox import InboxMsg, mailbox_registry

    async with async_unit_of_work(commit=False) as db:
        target_run = await TaskSessionDatastore(db).get_run(to_session_id)
    if target_run is None or target_run.task_id != task_id or target_run.project_id != project_id:
        return {
            "delivered": False,
            "error": (
                f"session {to_session_id!r} is not a member of this task — "
                "you can only send to members you dispatched in this task"
            ),
        }

    delivered = mailbox_registry.put(
        to_session_id,
        InboxMsg(kind="message", text=text, from_session=from_session_id),
    )
    if not delivered:
        return {
            "delivered": False,
            "error": (
                f"member session {to_session_id!r} is not running "
                "(already finished or never started)"
            ),
        }

    async with async_unit_of_work() as db:
        await TaskEventDatastore(db).append_event(
            user_id,
            project_id=project_id,
            task_id=task_id,
            type="subtask_message",
            actor="lead",
            session_id=to_session_id,
            payload={"direction": "lead->member", "text": text},
        )
    return {"delivered": True, "session_id": to_session_id}


async def inject_into_task(
    *,
    task_id: str,
    project_id: str,
    text: str,
    from_session_id: str,
    user_id: str,
) -> dict[str, Any]:
    """Inject a free-text instruction from a chat session into a running task's lead.

    VALUZ-CHATPLAN S4: chat sessions can talk to an already-committed task
    without taking the writer-gate lock (which is lead-only on active
    tasks). The text is wrapped in a ``<user-instruction source="chat">``
    envelope so the lead recognises it as authoritative user intent (see
    COMMITTED_LEAD_PLAYBOOK §8) and typically converts it into modify_plan
    + dispatch (or rework).

    Returns ``{delivered: bool, lead_session_id: str | None, reason: str | None}``:
      - active task + registered lead inbox → ``delivered=True``
      - paused / blocked / stopped task → the task is REVIVED
        (``resume_task`` with the text as the resume instruction) →
        ``delivered=True, reason=TASK_RESUMED``; a failed revive returns
        ``delivered=False, reason=RESUME_FAILED``
      - no lead run found for the task → ``delivered=False, reason=NO_LEAD``
      - lead run exists but mailbox unregistered (already finished) →
        ``delivered=False, reason=LEAD_OFFLINE``
      - draft / completed / abandoned task →
        ``delivered=False, reason=TASK_NOT_ACTIVE``
    """
    from valuz_agent.modules.tasks.mailbox import InboxMsg, mailbox_registry

    async with async_unit_of_work(commit=False) as db:
        task_row = await TaskDatastore(db).get_task_by_project(
            user_id, project_id, task_id
        )
    if task_row is None:
        return {
            "delivered": False,
            "lead_session_id": None,
            "reason": "TASK_NOT_FOUND",
        }
    if task_row.status in ("paused", "blocked", "stopped"):
        # Halted task: the lead loop is torn down and its mailbox is
        # unregistered, so a plain put() can never deliver. "Talking to a
        # halted task" IS the user's resume intent (the intervene docstring
        # promised "chat/inject can also revive it") — route through
        # resume_task with the text as the resume instruction: it flips the
        # status, reconciles members, and embeds the text in the respawned
        # lead's recovery brief. resume_task appends the ``resumed`` +
        # ``user_inject`` events itself — don't double-append here.
        # ``completed`` stays excluded: reopening a finished task is a
        # deliberate act (detail-page reopen / explicit resume_task), not a
        # side effect of a stray chat message.
        from valuz_agent.modules.tasks.orchestrator import task_orchestrator

        result = await task_orchestrator.resume_task(
            task_id, project_id, user_id=user_id, instruction=text
        )
        lead_session_id = None
        async with async_unit_of_work(commit=False) as db:
            runs = await TaskSessionDatastore(db).list_runs(user_id, task_id)
            lead = next((r for r in runs if r.kind == "lead"), None)
            if lead is not None:
                lead_session_id = lead.session_id
        if result.get("ok"):
            return {
                "delivered": True,
                "lead_session_id": lead_session_id,
                "reason": "TASK_RESUMED",
            }
        return {
            "delivered": False,
            "lead_session_id": lead_session_id,
            "reason": "RESUME_FAILED",
        }
    if task_row.status != "active":
        return {
            "delivered": False,
            "lead_session_id": None,
            "reason": "TASK_NOT_ACTIVE",
        }

    async with async_unit_of_work(commit=False) as db:
        runs = await TaskSessionDatastore(db).list_runs(user_id, task_id)
    lead_run = next((r for r in runs if r.kind == "lead"), None)
    if lead_run is None:
        return {
            "delivered": False,
            "lead_session_id": None,
            "reason": "NO_LEAD",
        }

    lead_session_id = lead_run.session_id
    wrapped = f'<user-instruction source="chat">\n{text}\n</user-instruction>'
    delivered = mailbox_registry.put(
        lead_session_id,
        InboxMsg(kind="message", text=wrapped, from_session=from_session_id),
    )

    async with async_unit_of_work() as db:
        event_ds = TaskEventDatastore(db)
        if delivered:
            await event_ds.append_event(
                user_id,
                project_id=project_id,
                task_id=task_id,
                type="user_inject",
                actor=from_session_id,
                session_id=lead_session_id,
                payload={"text": text, "lead_session_id": lead_session_id},
            )
        else:
            await event_ds.append_event(
                user_id,
                project_id=project_id,
                task_id=task_id,
                type="user_inject_dropped",
                actor=from_session_id,
                session_id=lead_session_id,
                payload={"text": text, "reason": "LEAD_OFFLINE"},
            )

    return {
        "delivered": delivered,
        "lead_session_id": lead_session_id,
        "reason": None if delivered else "LEAD_OFFLINE",
    }


async def record_awaiting_user(
    *,
    task_id: str,
    project_id: str,
    session_id: str,
    subtask_key: str | None,
    agent_slug: str,
    agent_name: str | None,
    question: str,
    pending_id: str,
    user_id: str,
) -> None:
    """Append an ``awaiting_user`` task event when an agent (lead or member)
    raises a question through the Decision Inbox.

    A pending question blocks the turn but leaves NO trace on the task's own
    timeline or status (the inbox is a cross-cutting overlay keyed by
    ``task_id``). Without this the task page shows "Running" while the task is
    actually blocked on the user. We do NOT add an ``awaiting_user`` task
    *status* (the task genuinely is still active and a status would need racy
    atomic clearing on answer) — this event is the timeline record + an SSE
    frame the attention surfaces drive from. Deduped by ``pending_id`` at the
    caller (the aggregator tracks emitted ids per process).
    """
    async with async_unit_of_work() as db:
        await TaskEventDatastore(db).append_event(
            user_id,
            project_id=project_id,
            task_id=task_id,
            type="awaiting_user",
            actor=agent_slug,
            session_id=session_id,
            payload={
                "agent_name": agent_name,
                "question": question,
                "pending_id": pending_id,
                **({"subtask_key": subtask_key} if subtask_key else {}),
            },
        )


async def record_user_answered(
    *,
    task_id: str,
    project_id: str,
    pending_id: str,
    session_id: str | None = None,
    user_id: str,
) -> None:
    """Append a ``user_answered`` task event when a pending question resolves
    (the counterpart to :func:`record_awaiting_user`)."""
    async with async_unit_of_work() as db:
        await TaskEventDatastore(db).append_event(
            user_id,
            project_id=project_id,
            task_id=task_id,
            type="user_answered",
            actor="user",
            session_id=session_id,
            payload={"pending_id": pending_id},
        )


async def notify_lead_goal_revised(
    *,
    task_id: str,
    project_id: str,
    new_goal: str,
    user_id: str,
) -> dict[str, Any]:
    """Wake a running task's lead after the user revised ``task.goal``.

    MVP for the "push, not pull" goal-revision gap: the goal is the lead's
    initial brief AND its goal-mode loop condition, both baked into the session
    at spawn — a bare ``task.goal`` DB write never reaches a running lead. We
    deliver a ``revise_goal`` mailbox message so the lead re-orients on its next
    turn boundary (and preempts an in-flight ``await_member_results``); the lead
    decides autonomously how to fold it in. Best-effort: a finished/offline lead
    returns ``delivered=False`` — the DB goal is still updated by the caller.

    The wrapper surfaces the goal-mode caveat to the lead: the kernel's
    auto-completion may still track the ORIGINAL goal, so this revision is
    declared authoritative.
    """
    from valuz_agent.modules.tasks.mailbox import InboxMsg, mailbox_registry

    async with async_unit_of_work(commit=False) as db:
        runs = await TaskSessionDatastore(db).list_runs(user_id, task_id)
    lead_run = next((r for r in runs if r.kind == "lead"), None)
    if lead_run is None:
        return {"delivered": False, "lead_session_id": None, "reason": "NO_LEAD"}

    lead_session_id = lead_run.session_id
    wrapped = (
        '<goal-revised source="user">\n'
        "The task goal has been revised by the user to:\n\n"
        f"{new_goal}\n\n"
        "Re-evaluate your current plan against this revised goal. If the plan has "
        "drifted, use modify_plan to adjust, then continue; call finish_task only "
        "when the REVISED goal is met. (Goal-mode auto-completion may still track "
        "the original goal — treat this revision as authoritative.)\n"
        "</goal-revised>"
    )
    delivered = mailbox_registry.put(
        lead_session_id,
        InboxMsg(kind="revise_goal", text=wrapped, payload={"goal": new_goal}),
    )
    return {
        "delivered": delivered,
        "lead_session_id": lead_session_id,
        "reason": None if delivered else "LEAD_OFFLINE",
    }
