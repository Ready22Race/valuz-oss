"""HTTP routes for lead-dispatch Tasks (lead-dispatch-mvp §S* / H9/H14).

Endpoints:
  POST   /v1/projects/{id}/tasks            — kickoff a task (goal + lead agent)
  POST   /v1/projects/{id}/tasks:draft      — open a draft task (VALUZ-CHATPLAN S3)
  GET    /v1/projects/{id}/tasks            — list project tasks
  GET    /v1/tasks/{task_id}                  — task header + runs + events
  GET    /v1/tasks/{task_id}/events           — full event log (ACTIVITY)
  GET    /v1/tasks/{task_id}/events/stream    — SSE: live task events (cursor: ?after_seq=N;
                                                terminal → ``stream_end`` unless ?keep_alive=1)
  POST   /v1/tasks/{task_id}:intervene        — note / revise_goal / pause / resume / stop
  POST   /v1/tasks/{task_id}:commit           — draft → active (VALUZ-CHATPLAN S3)
  POST   /v1/tasks/{task_id}:abandon          — draft → abandoned (VALUZ-CHATPLAN S3)
  POST   /v1/tasks/{task_id}:inject           — push user instruction into lead mailbox (S4)
  POST   /v1/tasks/{task_id}/plan             — lay down the initial plan
  PATCH  /v1/tasks/{task_id}/plan             — modify the plan (CAS via expected_version)
  GET    /v1/tasks/{task_id}/plan             — read the plan snapshot
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from valuz_agent.api.deps import get_current_user_id
from valuz_agent.infra.db import async_unit_of_work, get_async_session
from valuz_agent.infra.sse import shielded
from valuz_agent.modules.automations.datastore import AutomationDatastore
from valuz_agent.modules.tasks import messaging, planning
from valuz_agent.modules.tasks.datastore import (
    TaskDatastore,
    TaskEventDatastore,
    TaskSessionDatastore,
)
from valuz_agent.modules.tasks.models import TaskEventRow, TaskRow
from valuz_agent.modules.tasks.orchestrator import task_orchestrator

router = APIRouter(tags=["tasks"])


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------


class KickoffTaskRequest(BaseModel):
    goal: str
    lead_agent_slug: str
    refs: list[str] | None = None
    title: str | None = None
    created_by: str = "user"
    # Dispatch architecture (M10): "sync" (v1, lead drives one turn, dispatch
    # blocks) or "async" (v2, lead + members are persistent actors).
    dispatch_mode: Literal["sync", "async"] = "async"
    # Task-level worktree isolation (design §5): the whole task — lead and
    # every member — runs in ONE git worktree of the project repo; the
    # branch merges back (or is discarded) when the task ends. Requires the
    # project cwd to be inside a git repository (422 otherwise).
    worktree: bool = False


class TaskTrigger(BaseModel):
    """Resolved "who/what spawned this task" — drives the task-list "由 … 触发"
    line. ``type`` is one of user | chat | agent | automation; the source_* ids
    let the UI deep-link to the parent task / automation / conversation, and the
    resolved names spare the frontend a second lookup."""

    type: str
    source_task_id: str | None = None
    source_task_title: str | None = None
    source_agent_slug: str | None = None
    source_automation_id: str | None = None
    source_automation_name: str | None = None
    source_session_id: str | None = None


class TaskResponse(BaseModel):
    id: str
    project_id: str
    title: str
    goal: str
    status: str
    created_by: str
    lead_agent_slug: str
    current_holder: str
    file_path: str
    # Surfaced so the sidebar TASKS section can sort/group by recency
    # ("active just now" vs "completed yesterday").
    created_at: int
    updated_at: int
    # Resolved trigger provenance (attached by the route, not from the ORM row).
    trigger: TaskTrigger | None = None

    model_config = {"from_attributes": True}


async def _resolve_triggers(
    db: AsyncSession, user_id: str, rows: list[TaskRow]
) -> dict[str, TaskTrigger]:
    """Batch-resolve each task's trigger provenance into a render-ready
    ``TaskTrigger`` (parent-task titles + automation names fetched once)."""
    parent_ids = list({r.trigger_task_id for r in rows if r.trigger_task_id})
    automation_ids = list({r.trigger_automation_id for r in rows if r.trigger_automation_id})
    titles = await TaskDatastore(db).get_titles_by_ids(user_id, parent_ids) if parent_ids else {}
    automations = (
        await AutomationDatastore(db).get_names_by_ids(user_id, automation_ids)
        if automation_ids
        else {}
    )
    out: dict[str, TaskTrigger] = {}
    for r in rows:
        ttype = r.trigger_type or "user"
        trig = TaskTrigger(type=ttype)
        # The originating task (for tree nesting) can ride on ANY type: directly
        # for ``agent``, or transitively for ``automation`` (an agent in a task
        # ran the automation that spawned this one). Surface it whenever present.
        if r.trigger_task_id:
            trig.source_task_id = r.trigger_task_id
            trig.source_task_title = titles.get(r.trigger_task_id)
            trig.source_agent_slug = r.trigger_agent_slug
        if ttype == "automation":
            trig.source_automation_id = r.trigger_automation_id
            trig.source_automation_name = automations.get(r.trigger_automation_id or "")
        elif ttype == "chat":
            trig.source_session_id = (r.metadata_ or {}).get("originating_session_id")
        out[r.id] = trig
    return out


def _to_task_responses(rows: list[TaskRow], triggers: dict[str, TaskTrigger]) -> list[TaskResponse]:
    out: list[TaskResponse] = []
    for r in rows:
        resp = TaskResponse.model_validate(r)
        resp.trigger = triggers.get(r.id)
        out.append(resp)
    return out


async def _task_response_with_trigger(db: AsyncSession, user_id: str, row: TaskRow) -> TaskResponse:
    triggers = await _resolve_triggers(db, user_id, [row])
    return _to_task_responses([row], triggers)[0]


class RunResponse(BaseModel):
    id: str
    session_id: str
    agent_slug: str
    sequence: int
    kind: str
    status: str
    label: str | None
    goal: str | None
    dispatched_by: str | None
    project_mode: str
    run_dir: str | None
    result_manifest: dict[str, Any] | None

    model_config = {"from_attributes": True}


class EventResponse(BaseModel):
    id: str
    sequence: int
    type: str
    actor: str
    session_id: str | None
    payload: dict[str, Any]
    created_at: int

    model_config = {"from_attributes": True}


class TaskDetailResponse(BaseModel):
    task: TaskResponse
    runs: list[RunResponse]
    events: list[EventResponse]


class InterveneRequest(BaseModel):
    action: Literal["note", "revise_goal", "pause", "resume", "stop"]
    text: str | None = None
    goal: str | None = None


# ---- VALUZ-CHATPLAN S3 schemas --------------------------------------------


class DraftTaskRequest(BaseModel):
    goal: str
    lead_agent_slug: str
    originating_session_id: str
    refs: list[str] | None = None
    title: str | None = None


class DraftTaskResponse(BaseModel):
    task_id: str
    status: str
    plan_version: int
    title: str
    lead_agent_slug: str


class CommitTaskRequest(BaseModel):
    caller_session_id: str
    lead_agent_slug: str | None = None


class AbandonTaskRequest(BaseModel):
    caller_session_id: str
    reason: str | None = None


class InjectTaskRequest(BaseModel):
    text: str
    from_session_id: str


class InjectTaskResponse(BaseModel):
    delivered: bool
    lead_session_id: str | None = None
    reason: str | None = None


class PlanTaskRequest(BaseModel):
    """Used by both POST (initial plan) and PATCH (modify). ``lead_session_id``
    is the caller's session — for draft-mode chat sessions, the originating
    chat; for active-mode lead callers, the lead session id."""

    lead_session_id: str
    subtasks: list[dict[str, Any]] | None = None  # POST: initial plan
    add: list[dict[str, Any]] | None = None  # PATCH: add nodes
    update: list[dict[str, Any]] | None = None  # PATCH: patch nodes
    expected_version: int | None = None  # PATCH: optimistic-lock token


class PlanResponse(BaseModel):
    subtasks: list[dict[str, Any]]
    ready: list[str]
    counts: dict[str, int] | None = None
    all_done: bool | None = None
    current_version: int


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


@router.post("/v1/projects/{project_id}/tasks", status_code=201, response_model=TaskResponse)
async def kickoff_task(
    project_id: str,
    payload: KickoffTaskRequest,
    user_id: str = Depends(get_current_user_id),
) -> TaskResponse:
    """Create a task and start its lead session (lead self-dispatches sub-runs)."""
    try:
        row = await task_orchestrator.kickoff(
            project_id=project_id,
            goal=payload.goal,
            lead_agent_slug=payload.lead_agent_slug,
            refs=payload.refs,
            created_by=payload.created_by,
            title=payload.title,
            dispatch_mode=payload.dispatch_mode,
            worktree=payload.worktree,
            user_id=user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # A REST kickoff is a direct user action (no originating session), so the
    # trigger is always "user" — no source to resolve.
    resp = TaskResponse.model_validate(row)
    resp.trigger = TaskTrigger(type=row.trigger_type or "user")
    return resp


@router.get("/v1/projects/{project_id}/tasks", response_model=dict[str, list[TaskResponse]])
async def list_tasks(
    project_id: str,
    db: AsyncSession = Depends(get_async_session),
    user_id: str = Depends(get_current_user_id),
) -> dict[str, list[TaskResponse]]:
    rows = await TaskDatastore(db).list_tasks(user_id, project_id)
    triggers = await _resolve_triggers(db, user_id, rows)
    return {"tasks": _to_task_responses(rows, triggers)}


@router.get("/v1/tasks", response_model=dict[str, list[TaskResponse]])
async def list_all_tasks(
    limit: int = 50,
    db: AsyncSession = Depends(get_async_session),
    user_id: str = Depends(get_current_user_id),
) -> dict[str, list[TaskResponse]]:
    """Global cross-project task list, newest activity first. Powers the
    sidebar TASKS section so users see what's running regardless of which
    project page they're on."""
    rows = await TaskDatastore(db).list_all(user_id, limit=limit)
    triggers = await _resolve_triggers(db, user_id, rows)
    return {"tasks": _to_task_responses(rows, triggers)}


@router.get("/v1/tasks/{task_id}", response_model=TaskDetailResponse)
async def get_task(
    task_id: str,
    db: AsyncSession = Depends(get_async_session),
    user_id: str = Depends(get_current_user_id),
) -> TaskDetailResponse:
    task = await TaskDatastore(db).get_task(user_id, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    runs = await TaskSessionDatastore(db).list_runs(user_id, task_id)
    events = await TaskEventDatastore(db).list_events(user_id, task.project_id, task_id)
    return TaskDetailResponse(
        task=await _task_response_with_trigger(db, user_id, task),
        runs=[RunResponse.model_validate(r) for r in runs],
        events=[EventResponse.model_validate(e) for e in events],
    )


@router.get("/v1/tasks/{task_id}/events", response_model=dict[str, list[EventResponse]])
async def list_task_events(
    task_id: str,
    db: AsyncSession = Depends(get_async_session),
    user_id: str = Depends(get_current_user_id),
) -> dict[str, list[EventResponse]]:
    task = await TaskDatastore(db).get_task(user_id, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    events = await TaskEventDatastore(db).list_events(user_id, task.project_id, task_id)
    return {"events": [EventResponse.model_validate(e) for e in events]}


# SSE polling cadence — tight enough that ``inject_into_task`` → plan
# change → chat reaction feels snappy, loose enough that idle tasks
# don't hammer the DB. 500ms matches the kernel-event SSE legacy poll
# default and the user-perceived latency budget for a "live" UI.
_TASK_EVENTS_POLL_INTERVAL_S = 0.5

# Heartbeat keep-alive — sse-starlette / browsers may otherwise close
# an idle connection. 15s matches the kernel session SSE.
_TASK_EVENTS_HEARTBEAT_S = 15.0

# Task statuses whose stream is allowed to end: no further events can
# arrive without a revival event first. ``stopped`` is deliberately NOT
# here — a stopped task can be revived by chat/inject with no action from
# the subscriber, so its stream must stay open to deliver ``resumed``.
_TASK_TERMINAL_STATUSES = frozenset({"completed", "failed", "abandoned"})

# Event types that flip an open stream into / out of the terminal state
# (mirrors the status transitions above without extra status queries).
_TASK_TERMINAL_EVENT_TYPES = frozenset({"task_completed", "abandoned"})
_TASK_REVIVAL_EVENT_TYPES = frozenset({"resumed", "committed"})

# Once terminal, keep polling this long for trailing events before
# emitting ``stream_end`` and closing the connection.
_TASK_EVENTS_TERMINAL_LINGER_S = 5.0


async def _iter_task_events_sse(
    task_id: str,
    project_id: str,
    after_seq: int,
    user_id: str,
    is_disconnected: Callable[[], bool] | None = None,
    initial_status: str | None = None,
    keep_alive: bool = False,
) -> AsyncIterator[dict[str, str]]:
    """Polling iterator for task-event SSE.

    Yields ``{event, data, id}`` dicts compatible with sse-starlette's
    ``EventSourceResponse``. Each event:
      - ``event`` = the task_event type (``task_planned`` / ``committed`` /
        ``task_plan_update`` / ``user_inject`` / etc.)
      - ``data`` = JSON-encoded EventResponse
      - ``id``   = the sequence number, so a reconnecting client can pass
        ``?after_seq=<id>`` to resume without gaps

    Sends a heartbeat ``{event: 'heartbeat'}`` every ``_TASK_EVENTS_HEARTBEAT_S``
    seconds of silence so intermediaries (nginx, browsers) don't close the
    connection.

    Terminal close: once the task is terminal (``initial_status`` at connect,
    or a terminal event observed mid-stream) and the log has stayed silent for
    ``_TASK_EVENTS_TERMINAL_LINGER_S``, a final ``{event: 'stream_end'}`` is
    emitted and the generator returns. Browsers cap HTTP/1.1 connections at 6
    per host, so an immortal stream per finished task starves every other
    request the client makes. Closing is never lossy — a reconnecting client
    resumes from its ``after_seq`` cursor. ``keep_alive=True`` opts out for
    subscribers that need a finished task's stream (the completed-task
    follow-up chat listens for ``deliverable_updated``).

    Task events don't have an in-memory broadcast subscriber (unlike kernel
    events). DB polling at 500ms is cheap (single indexed query per tick)
    and exact (sequence is monotonic per task — no gaps possible).
    """
    cursor = after_seq
    silent_for = 0.0
    terminal = not keep_alive and initial_status in _TASK_TERMINAL_STATUSES
    terminal_silent = 0.0
    while True:
        if is_disconnected is not None and is_disconnected():
            return

        # ``shielded``: client disconnect cancels this generator; landing the
        # cancellation inside the in-flight DB read would tear the pooled
        # connection down mid-checkin (see ``infra.sse.shielded``).
        async def _tick_read(after: int) -> list[TaskEventRow]:
            async with async_unit_of_work(commit=False) as db:
                return await TaskEventDatastore(db).list_events_after(
                    user_id, project_id, task_id, after
                )

        rows = await shielded(_tick_read(cursor))
        if rows:
            for row in rows:
                event_payload = EventResponse.model_validate(row).model_dump(mode="json")
                yield {
                    "id": str(row.sequence),
                    "event": row.type,
                    "data": json.dumps(event_payload, ensure_ascii=False),
                }
                cursor = row.sequence
                if not keep_alive:
                    if row.type in _TASK_TERMINAL_EVENT_TYPES:
                        terminal = True
                    elif row.type in _TASK_REVIVAL_EVENT_TYPES:
                        terminal = False
            silent_for = 0.0
            terminal_silent = 0.0
        else:
            silent_for += _TASK_EVENTS_POLL_INTERVAL_S
            if terminal:
                terminal_silent += _TASK_EVENTS_POLL_INTERVAL_S
                if terminal_silent >= _TASK_EVENTS_TERMINAL_LINGER_S:
                    yield {"event": "stream_end", "data": ""}
                    return
            if silent_for >= _TASK_EVENTS_HEARTBEAT_S:
                yield {"event": "heartbeat", "data": ""}
                silent_for = 0.0
        await asyncio.sleep(_TASK_EVENTS_POLL_INTERVAL_S)


@router.get("/v1/tasks/{task_id}/events/stream")
async def stream_task_events(
    task_id: str,
    request: Request,
    after_seq: int = 0,
    keep_alive: bool = False,
    user_id: str = Depends(get_current_user_id),
) -> EventSourceResponse:
    """SSE subscription for a task's event timeline.

    Reconnect protocol: client passes ``?after_seq=<last_seen_id>`` to
    resume from the cursor. The server replays everything newer
    (no gaps possible — sequence is strictly monotonic). The
    ``id:`` field on each emitted event is the sequence number the
    client should remember for the next reconnect.

    Terminal tasks close the stream (final ``stream_end`` event) after a
    short linger, so finished tasks don't pin one of the browser's 6
    per-host connections forever. ``?keep_alive=1`` opts out — see
    ``_iter_task_events_sse``.

    Polling cadence: 500ms (see ``_TASK_EVENTS_POLL_INTERVAL_S``). The
    DB write side (``_emit_plan_update`` and friends) doesn't currently
    publish to an in-memory broadcast queue; once that lands (Slice 6
    optimization candidate) this endpoint can switch to push.
    """
    async with async_unit_of_work(commit=False) as db:
        task = await TaskDatastore(db).get_task(user_id, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    # ``request.is_disconnected`` is async; sse-starlette wraps the
    # generator with a cancel scope that fires on client drop, so we
    # don't need to pass a custom disconnect probe — ``None`` is fine.
    del request
    return EventSourceResponse(
        _iter_task_events_sse(
            task_id=task_id,
            project_id=task.project_id,
            after_seq=after_seq,
            user_id=user_id,
            is_disconnected=None,
            initial_status=task.status,
            keep_alive=keep_alive,
        )
    )


@router.post("/v1/tasks/{task_id}:intervene", response_model=TaskResponse)
async def intervene(
    task_id: str,
    payload: InterveneRequest,
    db: AsyncSession = Depends(get_async_session),
    user_id: str = Depends(get_current_user_id),
) -> TaskResponse:
    """User intervention on a running task.

    note          — append user_note (does not interrupt the lead)
    revise_goal   — update task.goal + append goal_revised
    pause         — cascade-halt the lead + every in-flight member → ``paused``
                    (recoverable; app-restart skips it, user resumes explicitly)
    stop          — cascade-halt → ``stopped`` (soft terminal; the detail page
                    offers a resume entry, and chat/inject can also revive it)
    resume        — reconcile + respawn members + re-drive lead
                    (paused/stopped/blocked → active)
    """
    task_ds = TaskDatastore(db)
    event_ds = TaskEventDatastore(db)
    task = await task_ds.get_task(user_id, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    ws = task.project_id

    if payload.action == "note":
        await event_ds.append_event(
            user_id,
            ws,
            task_id,
            "user_note",
            actor="user",
            payload={"text": payload.text or ""},
        )
    elif payload.action == "revise_goal":
        if not payload.goal:
            raise HTTPException(status_code=422, detail="goal is required for revise_goal")
        task.goal = payload.goal
        await task_ds.update_task(task)
        # Push the revision to the running lead so it actually re-orients —
        # task.goal alone is pull-only (the lead never re-reads it mid-run).
        # Best-effort: an offline/finished lead just isn't woken (DB goal still
        # updated above). The lead decides autonomously how to fold it in.
        notified = await messaging.notify_lead_goal_revised(
            task_id=task_id, project_id=ws, new_goal=payload.goal
        )
        await event_ds.append_event(
            user_id,
            ws,
            task_id,
            "goal_revised",
            actor="user",
            payload={"goal": payload.goal, "delivered_to_lead": notified["delivered"]},
        )
    elif payload.action in ("pause", "stop"):
        # Layer 2 cascade halt (orchestrator manages its own txn). ``pause`` →
        # ``paused``; ``stop`` → ``stopped``. Both are soft terminals the
        # detail page can resume (stopped→active is a legal transition).
        target = "paused" if payload.action == "pause" else "stopped"
        applied = await task_orchestrator.stop_task(
            task_id, ws, target_status=target, user_id=user_id
        )
        # ``stop_task`` returns False on an illegal transition (e.g. the task is
        # already terminal). Surface it instead of swallowing it — otherwise the
        # client toasts "已停止/已暂停" on a no-op while the badge keeps the old
        # status, the exact "状态有误" symptom the user hit.
        if not applied:
            raise HTTPException(
                status_code=409,
                detail=f"cannot {payload.action} task in status {task.status!r}",
            )
    elif payload.action == "resume":
        result = await task_orchestrator.resume_task(task_id, ws, user_id=user_id)
        # ``resume_task`` returns ``{ok: False, error}`` on an illegal source
        # state (e.g. resuming an ``active`` task). Same rationale as stop above.
        if not result.get("ok"):
            raise HTTPException(
                status_code=409,
                detail=result.get("error") or "cannot resume task",
            )

    db.expire_all()  # drop cached rows so we see the orchestrator's committed write
    refreshed = await task_ds.get_task(user_id, task_id)
    assert refreshed is not None
    return await _task_response_with_trigger(db, user_id, refreshed)


class StopMemberResponse(BaseModel):
    stopped: bool


# --------------------------------------------------------------------------
# VALUZ-CHATPLAN S3 — draft / commit / abandon / inject / plan REST routes
#
# These wrap the orchestrator methods that the MCP tool handlers also call
# (draft_task, commit_task, abandon_task, inject_into_task, plan_task,
# modify_plan, get_plan), so the frontend can drive the same state machine
# directly via HTTP without going through an agent turn.
# --------------------------------------------------------------------------


@router.post(
    "/v1/projects/{project_id}/tasks:draft",
    status_code=201,
    response_model=DraftTaskResponse,
)
async def draft_task(
    project_id: str,
    payload: DraftTaskRequest,
    user_id: str = Depends(get_current_user_id),
) -> DraftTaskResponse:
    """Open a draft task (status=draft, plan_version=0). No lead session is
    started — the originating chat session is recorded as the plan writer."""
    try:
        row = await task_orchestrator.draft_task(
            project_id=project_id,
            goal=payload.goal,
            lead_agent_slug=payload.lead_agent_slug,
            originating_session_id=payload.originating_session_id,
            refs=payload.refs,
            title=payload.title,
            user_id=user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return DraftTaskResponse(
        task_id=row.id,
        status=row.status,
        plan_version=row.plan_version or 0,
        title=row.title,
        lead_agent_slug=row.lead_agent_slug,
    )


@router.post("/v1/tasks/{task_id}:commit", response_model=dict[str, Any])
async def commit_task(
    task_id: str,
    payload: CommitTaskRequest,
    user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """Promote a draft task to active by spawning its lead session."""
    async with async_unit_of_work(commit=False) as db:
        task = await TaskDatastore(db).get_task(user_id, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    result = await task_orchestrator.commit_task(
        task_id=task_id,
        project_id=task.project_id,
        caller_session_id=payload.caller_session_id,
        lead_agent_slug_override=payload.lead_agent_slug,
        user_id=user_id,
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.post("/v1/tasks/{task_id}:abandon", response_model=dict[str, Any])
async def abandon_task(
    task_id: str,
    payload: AbandonTaskRequest,
    user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """Discard a draft task. Terminal (cannot be resurrected)."""
    async with async_unit_of_work(commit=False) as db:
        task = await TaskDatastore(db).get_task(user_id, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    result = await task_orchestrator.abandon_task(
        task_id=task_id,
        project_id=task.project_id,
        caller_session_id=payload.caller_session_id,
        reason=payload.reason or "",
        user_id=user_id,
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.post("/v1/tasks/{task_id}:inject", response_model=InjectTaskResponse)
async def inject_into_task(
    task_id: str,
    payload: InjectTaskRequest,
    user_id: str = Depends(get_current_user_id),
) -> InjectTaskResponse:
    """Push a user instruction into the lead session's mailbox. Returns
    delivered=False with reason when the lead is offline / task not active."""
    async with async_unit_of_work(commit=False) as db:
        task = await TaskDatastore(db).get_task(user_id, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    if not payload.text or not payload.text.strip():
        raise HTTPException(status_code=422, detail="text is required")
    result = await messaging.inject_into_task(
        task_id=task_id,
        project_id=task.project_id,
        text=payload.text,
        from_session_id=payload.from_session_id,
        user_id=user_id,
    )
    return InjectTaskResponse(
        delivered=bool(result.get("delivered")),
        lead_session_id=result.get("lead_session_id"),
        reason=result.get("reason"),
    )


@router.post("/v1/tasks/{task_id}/plan", response_model=PlanResponse)
async def plan_task_route(
    task_id: str,
    payload: PlanTaskRequest,
    user_id: str = Depends(get_current_user_id),
) -> PlanResponse:
    """Lay down the initial plan (errors if a plan with progress already exists)."""
    async with async_unit_of_work(commit=False) as db:
        task = await TaskDatastore(db).get_task(user_id, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    if not payload.subtasks:
        raise HTTPException(status_code=422, detail="subtasks is required and must be non-empty")
    result = await planning.plan_task(
        task_id=task_id,
        project_id=task.project_id,
        lead_session_id=payload.lead_session_id,
        subtasks=payload.subtasks,
        user_id=user_id,
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return PlanResponse(
        subtasks=result["subtasks"],
        ready=result["ready"],
        current_version=result["current_version"],
    )


@router.patch("/v1/tasks/{task_id}/plan", response_model=PlanResponse)
async def modify_plan_route(
    task_id: str,
    payload: PlanTaskRequest,
    user_id: str = Depends(get_current_user_id),
) -> PlanResponse:
    """Patch the plan: add nodes / update existing nodes. CAS via
    ``expected_version`` — returns 409 on conflict."""
    async with async_unit_of_work(commit=False) as db:
        task = await TaskDatastore(db).get_task(user_id, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    result = await planning.modify_plan(
        task_id=task_id,
        project_id=task.project_id,
        lead_session_id=payload.lead_session_id,
        add=payload.add,
        update=payload.update,
        expected_version=payload.expected_version,
        user_id=user_id,
    )
    if result.get("error") == "PLAN_VERSION_CONFLICT":
        raise HTTPException(status_code=409, detail=result)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return PlanResponse(
        subtasks=result["subtasks"],
        ready=result["ready"],
        current_version=result["current_version"],
    )


@router.get("/v1/tasks/{task_id}/plan", response_model=PlanResponse)
async def get_plan_route(
    task_id: str,
    user_id: str = Depends(get_current_user_id),
) -> PlanResponse:
    """Read the plan snapshot + ready keys + counts + current_version."""
    async with async_unit_of_work(commit=False) as db:
        task = await TaskDatastore(db).get_task(user_id, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    result = await planning.get_plan(
        task_id=task_id,
        project_id=task.project_id,
        user_id=user_id,
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return PlanResponse(
        subtasks=result["subtasks"],
        ready=result["ready"],
        counts=result.get("counts"),
        all_done=result.get("all_done"),
        current_version=result["current_version"],
    )


@router.post("/v1/runs/{session_id}:stop", response_model=StopMemberResponse)
async def stop_member(
    session_id: str,
    user_id: str = Depends(get_current_user_id),
) -> StopMemberResponse:
    """User-initiated single-member stop: interrupt one subtask, notify the lead
    (member_done cancelled), run→rejected, node→rework. Task stays active."""
    stopped = await task_orchestrator.stop_member(session_id, user_id=user_id)
    if not stopped:
        raise HTTPException(status_code=404, detail=f"Subtask run not found: {session_id}")
    return StopMemberResponse(stopped=True)
