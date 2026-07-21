"""Task tool gate POLICY — pure, I/O-free authorization rules.

Who may call which task tool is load-bearing policy. Keeping it pure —
plain functions over already-loaded session/task objects, returning an error
*string* (or the granted value) — makes the rules unit-testable without DB or
transport fixtures, and keeps them portable (task-kernel-migration.md D5 would
move them with the tool surface; that migration is currently deferred).
``handlers.py`` owns the reads and wraps error strings for the wire.

Rules mirror VALUZ-CHATPLAN D4/D6 and M10 附录 E — see each function.
"""

from __future__ import annotations

from typing import Any


def _valuz_meta(sess: Any) -> dict[str, Any]:
    meta = getattr(sess, "metadata", None) or {}
    v = meta.get("valuz", {})
    return v if isinstance(v, dict) else {}


def check_lead_gate(sess: Any) -> tuple[str, str] | str:
    """Lead-only tools (dispatch / await_members / send / review / finish).

    Returns ``(task_id, project_id)`` when *sess* is a lead session with its
    task binding intact, else the rejection reason.
    """
    v = _valuz_meta(sess)
    if v.get("run_kind") != "lead":
        return "only the lead session may call dispatch tools"
    task_id = v.get("task_id", "")
    project_id = v.get("project_id", "")
    if not task_id or not project_id:
        return "dispatch: lead session is missing task_id or project_id in metadata"
    return task_id, project_id


def check_plan_writer_gate(sess: Any, task: Any) -> str | None:
    """May *sess* write plan / state on *task*? ``None`` = allowed.

    Policy (VALUZ-CHATPLAN D6 strict):
      - ``status == draft``: originating session OR any session in the task's
        project (personal-desktop trust boundary — Q3).
      - ``status == active``: STRICT lead-only. Chat that wants to revise the
        plan mid-execution must go through ``inject_into_task`` (S4) and let
        the lead make the change itself.
      - ``status == paused``: read-only; resume the task to edit.
      - ``status in (completed, stopped, blocked, abandoned)``: read-only.
    """
    v = _valuz_meta(sess)
    if task.status == "draft":
        meta = task.metadata_ or {}
        origin = meta.get("originating_session_id")
        if sess.id == origin:
            return None
        caller_ws = getattr(sess, "project_id", "") or v.get("project_id", "")
        if caller_ws == task.project_id:
            return None
        return (
            f"not authorized: draft task {task.id!r} is held by its originator and "
            f"project members; caller is in project {caller_ws!r}, task is in "
            f"{task.project_id!r}"
        )
    if task.status == "active":
        if v.get("run_kind") == "lead" and v.get("task_id") == task.id:
            return None
        return (
            "active task plan is lead-owned; chat sessions must use "
            "inject_into_task to ask the lead to revise it (D6 strict)"
        )
    if task.status == "paused":
        return f"task {task.id!r} is paused; resume it before editing the plan"
    return f"task {task.id!r} is {task.status!r}; plan is read-only"


def check_plan_reader_gate(sess: Any, task: Any) -> str | None:
    """Loose read-only variant: any caller in the task's project may read."""
    v = _valuz_meta(sess)
    caller_ws = getattr(sess, "project_id", "") or v.get("project_id", "")
    if caller_ws != task.project_id:
        return (
            f"plan tool: caller project {caller_ws!r} does not match "
            f"task project {task.project_id!r}"
        )
    return None


def check_orchestration_caller(sess: Any) -> tuple[str, str] | str:
    """Session-shape half of the ``create_task`` gate (M10 附录 E).

    Allowed only from a plain project conversation: the session must carry a
    project and must NOT already be a task session (lead/subtask) — a task
    may not recursively spawn nested tasks (E-3). Returns
    ``(project_id, agent_slug)``; the caller still verifies the project row
    is a real project (that check needs the DB and stays in handlers).
    """
    v = _valuz_meta(sess)
    if v.get("run_kind") in ("lead", "subtask"):
        return (
            "create_task is only available in a project conversation, not "
            "inside a running task (nested tasks are not supported)"
        )
    # Project = the kernel Session.project_id (authoritative). Plain
    # conversation sessions don't echo project_id into valuz metadata, so
    # read project_id directly (valuz.project_id only exists on task runs).
    project_id = getattr(sess, "project_id", "") or v.get("project_id", "")
    if not project_id:
        return "create_task: caller session has no project"
    return project_id, v.get("agent_slug") or ""


__all__ = [
    "check_lead_gate",
    "check_orchestration_caller",
    "check_plan_reader_gate",
    "check_plan_writer_gate",
]
