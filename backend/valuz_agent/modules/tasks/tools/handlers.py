"""Dispatch MCP tool HANDLERS — the thin args → service-call → ToolResult shims.

Holds ``build_task_tool_defs`` (the public entry point — it returns the full
``ToolDef`` tuple the host's toolkit MCP server serves), the lead /
plan-writer / orchestration gate helpers, and the async closure handlers.

Each handler is a thin translate-args → composition-root method call →
``ToolResult`` shim — the business logic lives on the peeled services behind
the ``TaskOrchestrator`` composition root (split shape A: the root exposes the
same method names the closures call, delegating to dispatcher / lifecycle /
coordination / recovery).

Static declarations (tool names, parameter schemas, ``ToolDef(handler=None)``)
live in ``declarations.py``.
"""

# ruff: noqa: I001
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import valuz_agent.boot.kernel  # noqa: F401

from src.core import ToolDef, ToolResult
from src.core.tools import ExecContext, ToolHandler

from valuz_agent.adapters.data_reader import data_reader
from valuz_agent.modules.tasks import messaging, planning, queries

from valuz_agent.modules.tasks.tools.declarations import (
    ABANDON_TASK_TOOL_DECLARATION,
    ABANDON_TASK_TOOL_NAME,
    AWAIT_MEMBERS_TOOL_DECLARATION,
    AWAIT_MEMBERS_TOOL_NAME,
    COMMIT_TASK_TOOL_DECLARATION,
    COMMIT_TASK_TOOL_NAME,
    CREATE_TASK_TOOL_DECLARATION,
    CREATE_TASK_TOOL_NAME,
    DISPATCH_TOOL_DECLARATION,
    DISPATCH_TOOL_NAME,
    DRAFT_TASK_TOOL_DECLARATION,
    DRAFT_TASK_TOOL_NAME,
    FINISH_TASK_TOOL_DECLARATION,
    FINISH_TASK_TOOL_NAME,
    GET_PLAN_TOOL_DECLARATION,
    GET_PLAN_TOOL_NAME,
    GET_TASK_TOOL_DECLARATION,
    GET_TASK_TOOL_NAME,
    INJECT_INTO_TASK_TOOL_DECLARATION,
    INJECT_INTO_TASK_TOOL_NAME,
    LIST_MEMBERS_TOOL_DECLARATION,
    LIST_MEMBERS_TOOL_NAME,
    LIST_TASKS_TOOL_DECLARATION,
    LIST_TASKS_TOOL_NAME,
    MODIFY_PLAN_TOOL_DECLARATION,
    MODIFY_PLAN_TOOL_NAME,
    PLAN_TASK_TOOL_DECLARATION,
    PLAN_TASK_TOOL_NAME,
    RESUME_TASK_TOOL_DECLARATION,
    RESUME_TASK_TOOL_NAME,
    REVIEW_SUBTASK_TOOL_DECLARATION,
    REVIEW_SUBTASK_TOOL_NAME,
    SEND_TOOL_DECLARATION,
    SEND_TOOL_NAME,
    STOP_SUBTASK_TOOL_DECLARATION,
    STOP_SUBTASK_TOOL_NAME,
    UPDATE_DELIVERABLE_TOOL_DECLARATION,
    UPDATE_DELIVERABLE_TOOL_NAME,
    _ABANDON_TASK_PARAMETERS,
    _AWAIT_MEMBERS_PARAMETERS,
    _COMMIT_TASK_PARAMETERS,
    _CREATE_TASK_PARAMETERS,
    _DISPATCH_PARAMETERS,
    _DRAFT_TASK_PARAMETERS,
    _FINISH_TASK_PARAMETERS,
    _GET_PLAN_PARAMETERS,
    _GET_TASK_PARAMETERS,
    _INJECT_INTO_TASK_PARAMETERS,
    _LIST_MEMBERS_PARAMETERS,
    _LIST_TASKS_PARAMETERS,
    _MODIFY_PLAN_PARAMETERS,
    _PLAN_TASK_PARAMETERS,
    _RESUME_TASK_PARAMETERS,
    _REVIEW_SUBTASK_PARAMETERS,
    _SEND_PARAMETERS,
    _STOP_SUBTASK_PARAMETERS,
    _UPDATE_DELIVERABLE_PARAMETERS,
)

if TYPE_CHECKING:
    from valuz_agent.modules.tasks.orchestrator import TaskOrchestrator

from valuz_agent.modules.tasks.tools import gate

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lead gate helper
# ---------------------------------------------------------------------------


async def _check_lead_gate(ctx: ExecContext) -> tuple[str, str] | ToolResult:
    """Verify the caller is a lead session and return (task_id, project_id).

    I/O wrapper: loads the caller session, applies the pure policy in
    ``tools/gate.py`` and wraps a rejection into ToolResult(is_error=True).
    """
    # Source the caller from the per-request built-in MCP context when not
    # passed explicitly (the toolkit MCP server publishes the session owner).
    user_id = ctx.user_id
    sess = await data_reader().get_session(user_id, ctx.session_id)
    if sess is None:
        return ToolResult(content="dispatch: caller session not found", is_error=True)
    verdict = gate.check_lead_gate(sess)
    if isinstance(verdict, str):
        return ToolResult(content=verdict, is_error=True)
    return verdict


async def _resolve_plan_writer_task(
    ctx: ExecContext, args: dict[str, Any]
) -> tuple[Any, str, str] | ToolResult:
    """Resolve the target task for a plan-writing call + verify the caller may write it.

    VALUZ-CHATPLAN D4 + D6: plan tools (plan_task / modify_plan / get_plan) and
    state-transition tools (draft_task / commit_task / abandon_task) are
    callable from BOTH chat and lead sessions. This helper:

    1. Picks the task_id from explicit ``args["task_id"]`` (chat path) or
       from the caller session's metadata (lead path).
    2. Loads the TaskRow.
    3. Runs the writer gate based on task.status:
       - ``draft``  → caller must be originating session OR same project.
       - ``active`` → caller must be the lead (strict D6).
       - else      → reject (plan is read-only on terminal/paused tasks).

    Returns ``(task_row, project_id, task_id)`` on success or
    ``ToolResult(is_error=True)`` on any failure.

    Read-only callers (get_plan) should use ``_resolve_plan_reader_task`` instead.
    """
    user_id = ctx.user_id
    sess = await data_reader().get_session(user_id, ctx.session_id)
    if sess is None:
        return ToolResult(content="plan tool: caller session not found", is_error=True)

    v: dict[str, Any] = (sess.metadata or {}).get("valuz", {})
    task_id = args.get("task_id") or v.get("task_id") or ""
    if not task_id:
        return ToolResult(
            content=(
                "plan tool: task_id is required (chat callers must pass it explicitly; "
                "lead callers must have it in session metadata)"
            ),
            is_error=True,
        )

    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.modules.tasks.datastore import TaskDatastore

    async with async_unit_of_work(commit=False) as db:
        task_ds = TaskDatastore(db)
        task = await task_ds.get_task(user_id, task_id)
    if task is None:
        return ToolResult(content=f"plan tool: task {task_id!r} not found", is_error=True)

    gate_err = _check_plan_writer_gate(sess, task)
    if gate_err is not None:
        return gate_err
    return task, task.project_id, task_id


async def _resolve_plan_reader_task(
    ctx: ExecContext, args: dict[str, Any]
) -> tuple[Any, str, str] | ToolResult:
    """Loose variant of ``_resolve_plan_writer_task`` for read-only plan calls.

    Permits any caller in the task's project (chat or lead). Useful for
    get_plan: knowing your own draft / a project mate's plan is fine.
    """
    user_id = ctx.user_id
    sess = await data_reader().get_session(user_id, ctx.session_id)
    if sess is None:
        return ToolResult(content="plan tool: caller session not found", is_error=True)

    v: dict[str, Any] = (sess.metadata or {}).get("valuz", {})
    task_id = args.get("task_id") or v.get("task_id") or ""
    if not task_id:
        return ToolResult(content="plan tool: task_id is required", is_error=True)

    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.modules.tasks.datastore import TaskDatastore

    async with async_unit_of_work(commit=False) as db:
        task_ds = TaskDatastore(db)
        task = await task_ds.get_task(user_id, task_id)
    if task is None:
        return ToolResult(content=f"plan tool: task {task_id!r} not found", is_error=True)

    reason = gate.check_plan_reader_gate(sess, task)
    if reason is not None:
        return ToolResult(content=reason, is_error=True)
    return task, task.project_id, task_id


def _check_plan_writer_gate(sess: Any, task: Any) -> ToolResult | None:
    """Verify ``sess`` is allowed to write plan / state on ``task``.

    Pure policy lives in ``tools/gate.py`` (VALUZ-CHATPLAN D6 strict) — this
    wrapper only shapes the rejection for the tool wire.
    """
    reason = gate.check_plan_writer_gate(sess, task)
    if reason is not None:
        return ToolResult(content=reason, is_error=True)
    return None


async def _check_orchestration_gate(ctx: ExecContext) -> tuple[str, str] | ToolResult:
    """Gate for ``create_task`` (M10 附录 E). Returns (project_id, agent_slug).

    Allowed only from a **plain project conversation** session: it must carry a
    ``project_id`` and must NOT already be a task session (``run_kind`` in
    {lead, subtask}) — that prevents a task lead/member from recursively
    spawning nested tasks (附录 E E-3). The project must be a project (chat
    projects are ephemeral). Returns a ToolResult(is_error=True) on failure.
    """
    user_id = ctx.user_id
    sess = await data_reader().get_session(user_id, ctx.session_id)
    if sess is None:
        return ToolResult(content="create_task: caller session not found", is_error=True)

    verdict = gate.check_orchestration_caller(sess)
    if isinstance(verdict, str):
        return ToolResult(content=verdict, is_error=True)
    project_id, agent_slug = verdict

    # Restrict to projects — chat projects are per-session ephemeral.
    # (Needs the DB, so it stays outside the pure policy; project knowledge
    # is read through the resolver seam.)
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.modules.tasks.resolution import task_session_resolver

    async with async_unit_of_work(commit=False) as db:
        env = await task_session_resolver.resolve_project_env(
            db, user_id=sess.user_id, project_id=project_id
        )
    if env is None or env.project_row.kind != "project":
        return ToolResult(
            content="create_task is only available inside a project",
            is_error=True,
        )

    return project_id, agent_slug


async def _bound_agent_member(sess: Any) -> dict[str, Any] | None:
    """The conversation's own bound agent, shaped like a ``list_members`` row.

    A project-less *chat* project has no deployed project members, but the
    conversation is still driven by a real agent — its bound library agent
    (e.g. the seeded ``default-assistant``), recorded on the session as
    ``metadata["valuz"]["agent_slug"]`` with the kernel agent at
    ``session.agent_id``. ``_list_members_handler`` surfaces it as a fallback
    so the roster isn't an empty dead-end the caller gives up on — the slug is
    directly usable as an automation's ``agent_slug``. Returns ``None`` when
    the session carries no bound agent slug.
    """
    from valuz_agent.adapters.agent_resolver import summarize_role

    valuz = (getattr(sess, "metadata", None) or {}).get("valuz", {})
    slug = valuz.get("agent_slug") if isinstance(valuz, dict) else None
    if not slug:
        return None
    agent_cfg = getattr(sess, "agent_config", None)
    return {
        "slug": slug,
        "name": agent_cfg.name if agent_cfg else slug,
        "runtime": agent_cfg.runtime_provider if agent_cfg else "unknown",
        "source_agent_slug": slug,
        "role_summary": summarize_role(agent_cfg.instructions) if agent_cfg else "",
    }


# ---------------------------------------------------------------------------
# Pull-gap: surface a member_done that landed between two await_members calls
# ---------------------------------------------------------------------------


def _with_inbox_notice(handler: ToolHandler) -> ToolHandler:
    """Wrap a lead tool so a member_done that arrived in the gap is surfaced.

    Companion to ``await_member_results``' ``still_running`` hint. Between two
    ``await_members`` calls the lead often touches other orchestration tools
    (get_plan / dispatch / review_subtask …). If a member finished meanwhile,
    its ``member_done`` sits in the lead's mailbox unread until the *next*
    ``await_members`` — the exact pull-gap that stranded a completed result for
    minutes. This wrapper PEEKS the mailbox (``has_pending`` — non-consuming: it
    never pops, so it cannot disturb ``await_member_results``' live-key /
    in_review invariants) and appends an ``inbox_pending`` notice to the JSON
    envelope so the model collects it NOW rather than reasoning past it.

    Self-gating: only fires for a caller whose session has queued mail (a lead in
    an active task); a no-op for chat callers, empty inboxes, error results, and
    non-JSON/plain-text envelopes.
    """
    import json as _json

    async def _wrapped(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
        result = await handler(args, ctx)
        try:
            if result.is_error:
                return result
            from valuz_agent.modules.tasks.mailbox import mailbox_registry

            if not mailbox_registry.has_pending(ctx.session_id):
                return result
            try:
                payload = _json.loads(result.content)
            except (ValueError, TypeError):
                return result  # plain-text envelope (e.g. finish_task) — leave as-is
            if not isinstance(payload, dict):
                return result
            payload["inbox_pending"] = True
            payload["inbox_hint"] = (
                "A member finished (or a user message arrived) and is waiting in "
                "your inbox. Call await_members now to collect and review it "
                "before continuing — a completed result is already queued and "
                "returns to you instantly."
            )
            return ToolResult(content=_json.dumps(payload, ensure_ascii=False), is_error=False)
        except Exception:  # noqa: BLE001 — a notice must never break the tool call
            return result

    return _wrapped


# Lead tools worth augmenting: those a lead can call *during* a wait gap. Excludes
# await_members (the consumer itself), terminal/plain-text tools (finish_task,
# update_deliverable), and chat-side orchestration tools (create/list/get_task,
# draft/commit/abandon/inject/resume) a running lead never calls.
_INBOX_NOTICE_TOOLS: frozenset[str] = frozenset(
    {
        DISPATCH_TOOL_NAME,
        GET_PLAN_TOOL_NAME,
        MODIFY_PLAN_TOOL_NAME,
        REVIEW_SUBTASK_TOOL_NAME,
        SEND_TOOL_NAME,
        STOP_SUBTASK_TOOL_NAME,
        PLAN_TASK_TOOL_NAME,
        LIST_MEMBERS_TOOL_NAME,
    }
)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def build_task_tool_defs(orchestrator: TaskOrchestrator) -> tuple[ToolDef, ...]:
    """Build the full task tool set (dispatch + orchestration) with live handlers.

    Captures *orchestrator* in closures so the handlers can reach host
    singletons without importing at module level (avoids circular imports
    at startup). The host toolkit MCP server partitions the returned defs
    into its ``base`` / ``lead`` toolsets by declaration name.

    Must be called after ``init_kernel_dependencies()`` (i.e. from the
    async startup steps), once the tasks orchestrator exists.
    """
    import json
    from dataclasses import replace as _dc_replace

    _defs: list[ToolDef] = []

    def register_tool(td: ToolDef) -> None:
        # Local shadow of the former kernel-registry call — the body below
        # is unchanged; defs are collected and served over the host's
        # toolkit MCP server instead of the kernel's in-process registry.
        # Pull-gap: wrap gap-callable lead tools so a member_done queued between
        # await_members calls is surfaced on the next tool result (self-gating).
        if td.name in _INBOX_NOTICE_TOOLS and td.handler is not None:
            td = _dc_replace(td, handler=_with_inbox_notice(td.handler))
        _defs.append(td)

    async def _dispatch_handler(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
        gate = await _check_lead_gate(ctx)
        if isinstance(gate, ToolResult):
            return gate
        task_id, project_id = gate

        subtask_key: str = (args.get("subtask_key") or "").strip()
        if not subtask_key:
            return ToolResult(content="dispatch: 'subtask_key' is required", is_error=True)
        try:
            # v0.14: dispatch is NON-BLOCKING — spawn the member actor and
            # return its handle immediately. The lead collects results in the
            # same turn via ``await_members``.
            result = await orchestrator.dispatch_async(
                task_id=task_id,
                project_id=project_id,
                lead_session_id=ctx.session_id,
                subtask_key=subtask_key,
                agent=args.get("agent"),
                goal=args.get("goal"),
                refs=args.get("refs") or [],
                project_mode=args.get("project_mode"),
                user_id=ctx.user_id,
            )
            return ToolResult(
                content=json.dumps(result, ensure_ascii=False), is_error="error" in result
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("dispatch handler error for task %s", task_id)
            return ToolResult(content=f"dispatch failed: {exc}", is_error=True)

    async def _await_members_handler(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
        gate = await _check_lead_gate(ctx)
        if isinstance(gate, ToolResult):
            return gate
        task_id, project_id = gate
        try:
            result = await orchestrator.await_member_results(
                lead_session_id=ctx.session_id,
                project_id=project_id,
                task_id=task_id,
                keys=args.get("keys"),
                # Default to "any" for immediate per-member review: return as
                # soon as one member finishes so the lead reviews it without
                # waiting for the slowest sibling. Loop to collect the rest.
                mode=args.get("mode") or "any",
                timeout_s=args.get("timeout_s"),
                user_id=ctx.user_id,
            )
            return ToolResult(
                content=json.dumps(result, ensure_ascii=False),
                is_error="error" in result,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("await_members handler error for task %s", task_id)
            return ToolResult(content=f"await_members failed: {exc}", is_error=True)

    async def _send_handler(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
        gate = await _check_lead_gate(ctx)
        if isinstance(gate, ToolResult):
            return gate
        task_id, project_id = gate

        to_session_id: str = args.get("session_id", "")
        text: str = args.get("text", "")
        if not to_session_id or not text:
            return ToolResult(content="send: session_id and text are required", is_error=True)

        try:
            result = await messaging.send_to_member(
                from_session_id=ctx.session_id,
                to_session_id=to_session_id,
                text=text,
                project_id=project_id,
                task_id=task_id,
                user_id=ctx.user_id,
            )
            return ToolResult(
                content=json.dumps(result, ensure_ascii=False),
                is_error=not result.get("delivered"),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("send handler error for task %s", task_id)
            return ToolResult(content=f"send failed: {exc}", is_error=True)

    # -- VALUZ-CHATPLAN S2 state-transition handlers ----------------------

    async def _draft_task_handler(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
        gate = await _check_orchestration_gate(ctx)
        if isinstance(gate, ToolResult):
            return gate
        project_id, conversation_agent_slug = gate

        goal: str = (args.get("goal") or "").strip()
        if not goal:
            return ToolResult(content="draft_task: goal is required", is_error=True)
        lead_agent: str = (args.get("lead_agent_slug") or conversation_agent_slug or "").strip()
        if not lead_agent:
            return ToolResult(
                content="draft_task: no lead_agent_slug given and conversation has no agent",
                is_error=True,
            )

        try:
            task_row = await orchestrator.draft_task(
                project_id=project_id,
                goal=goal,
                lead_agent_slug=lead_agent,
                originating_session_id=ctx.session_id,
                refs=args.get("refs") or [],
                title=args.get("title"),
                user_id=ctx.user_id,
            )
            return ToolResult(
                content=json.dumps(
                    {
                        "task_id": task_row.id,
                        "title": task_row.title,
                        "lead_agent_slug": lead_agent,
                        "status": "draft",
                        "plan_version": task_row.plan_version,
                    },
                    ensure_ascii=False,
                )
            )
        except ValueError as exc:
            return ToolResult(content=f"draft_task: {exc}", is_error=True)
        except Exception as exc:  # noqa: BLE001
            logger.exception("draft_task handler error in project %s", project_id)
            return ToolResult(content=f"draft_task failed: {exc}", is_error=True)

    async def _commit_task_handler(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
        # commit_task is the writer-gate-protected state transition: only the
        # draft's originator (or a same-project chat) can flip it active.
        user_id = ctx.user_id
        resolved = await _resolve_plan_writer_task(ctx, args)
        if isinstance(resolved, ToolResult):
            return resolved
        task, project_id, task_id = resolved
        try:
            result = await orchestrator.commit_task(
                task_id=task_id,
                project_id=project_id,
                caller_session_id=ctx.session_id,
                lead_agent_slug_override=args.get("lead_agent_slug"),
                user_id=user_id,
            )
            return ToolResult(
                content=json.dumps(result, ensure_ascii=False),
                is_error="error" in result,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("commit_task handler error for task %s", task_id)
            return ToolResult(content=f"commit_task failed: {exc}", is_error=True)

    async def _abandon_task_handler(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
        user_id = ctx.user_id
        resolved = await _resolve_plan_writer_task(ctx, args)
        if isinstance(resolved, ToolResult):
            return resolved
        task, project_id, task_id = resolved
        try:
            result = await orchestrator.abandon_task(
                task_id=task_id,
                project_id=project_id,
                caller_session_id=ctx.session_id,
                reason=(args.get("reason") or ""),
                user_id=user_id,
            )
            return ToolResult(
                content=json.dumps(result, ensure_ascii=False),
                is_error="error" in result,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("abandon_task handler error for task %s", task_id)
            return ToolResult(content=f"abandon_task failed: {exc}", is_error=True)

    async def _inject_into_task_handler(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
        # VALUZ-CHATPLAN S4: chat → running-lead intervention. Auth is looser
        # than the writer gate (a chat session may not be the originator AND
        # the task is past draft) — project-member is enough because the
        # lead retains full authority over what to do with the message.
        user_id = ctx.user_id
        task_id = (args.get("task_id") or "").strip()
        text = args.get("text") or ""
        if not task_id:
            return ToolResult(content="inject_into_task: task_id is required", is_error=True)
        if not text.strip():
            return ToolResult(content="inject_into_task: text is required", is_error=True)

        sess = await data_reader().get_session(user_id, ctx.session_id)
        if sess is None:
            return ToolResult(content="inject_into_task: caller session not found", is_error=True)

        from valuz_agent.infra.db import async_unit_of_work
        from valuz_agent.modules.tasks.datastore import TaskDatastore

        async with async_unit_of_work(commit=False) as db:
            task = await TaskDatastore(db).get_task(user_id, task_id)
        if task is None:
            return ToolResult(
                content=f"inject_into_task: task {task_id!r} not found", is_error=True
            )

        v: dict[str, Any] = (sess.metadata or {}).get("valuz", {})
        caller_ws = getattr(sess, "project_id", "") or v.get("project_id", "")
        origin = (task.metadata_ or {}).get("originating_session_id")
        is_originator = bool(origin) and sess.id == origin
        is_project_mate = bool(caller_ws) and caller_ws == task.project_id
        if not (is_originator or is_project_mate):
            return ToolResult(
                content=(
                    f"inject_into_task: FORBIDDEN — caller is neither the task's "
                    f"originator nor a session in the task's project "
                    f"(task project {task.project_id!r})"
                ),
                is_error=True,
            )

        try:
            result = await messaging.inject_into_task(
                task_id=task_id,
                project_id=task.project_id,
                text=text,
                from_session_id=ctx.session_id,
                user_id=ctx.user_id,
            )
            return ToolResult(
                content=json.dumps(result, ensure_ascii=False),
                is_error=not result.get("delivered"),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("inject_into_task handler error for task %s", task_id)
            return ToolResult(content=f"inject_into_task failed: {exc}", is_error=True)

    async def _resume_task_handler(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
        # Chat-side resume. Same auth model as inject_into_task: caller must
        # be the task's originator OR a session in the same project —
        # state-machine + orchestrator.resume_task already validates that the
        # task is paused/blocked (terminal/draft/active rejected with a
        # human-readable reason in the dict it returns).
        user_id = ctx.user_id
        task_id = (args.get("task_id") or "").strip()
        if not task_id:
            return ToolResult(content="resume_task: task_id is required", is_error=True)

        sess = await data_reader().get_session(user_id, ctx.session_id)
        if sess is None:
            return ToolResult(content="resume_task: caller session not found", is_error=True)

        from valuz_agent.infra.db import async_unit_of_work
        from valuz_agent.modules.tasks.datastore import TaskDatastore

        async with async_unit_of_work(commit=False) as db:
            task = await TaskDatastore(db).get_task(user_id, task_id)
        if task is None:
            return ToolResult(content=f"resume_task: task {task_id!r} not found", is_error=True)

        v: dict[str, Any] = (sess.metadata or {}).get("valuz", {})
        caller_ws = getattr(sess, "project_id", "") or v.get("project_id", "")
        origin = (task.metadata_ or {}).get("originating_session_id")
        is_originator = bool(origin) and sess.id == origin
        is_project_mate = bool(caller_ws) and caller_ws == task.project_id
        if not (is_originator or is_project_mate):
            return ToolResult(
                content=(
                    "resume_task: FORBIDDEN — caller is neither the task's "
                    "originator nor a session in the task's project "
                    f"(task project {task.project_id!r})"
                ),
                is_error=True,
            )

        try:
            result = await orchestrator.resume_task(
                task_id=task_id,
                project_id=task.project_id,
                actor=ctx.session_id,
                user_id=user_id,
            )
            return ToolResult(
                content=json.dumps(result, ensure_ascii=False),
                is_error=not result.get("ok"),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("resume_task handler error for task %s", task_id)
            return ToolResult(content=f"resume_task failed: {exc}", is_error=True)

    async def _create_task_handler(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
        gate = await _check_orchestration_gate(ctx)
        if isinstance(gate, ToolResult):
            return gate
        project_id, conversation_agent_slug = gate

        goal: str = (args.get("goal") or "").strip()
        if not goal:
            return ToolResult(content="create_task: goal is required", is_error=True)
        lead_agent: str = (args.get("lead_agent") or conversation_agent_slug or "").strip()
        if not lead_agent:
            return ToolResult(
                content="create_task: no lead_agent given and conversation has no agent",
                is_error=True,
            )
        try:
            task_row = await orchestrator.kickoff(
                project_id=project_id,
                goal=goal,
                lead_agent_slug=lead_agent,
                refs=args.get("refs") or [],
                created_by=ctx.session_id,
                title=args.get("title"),
                originating_session_id=ctx.session_id,
                user_id=ctx.user_id,
            )
            return ToolResult(
                content=json.dumps(
                    {
                        "task_id": task_row.id,
                        "title": task_row.title,
                        "lead_agent": lead_agent,
                        "status": "active",
                    },
                    ensure_ascii=False,
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("create_task handler error in project %s", project_id)
            return ToolResult(content=f"create_task failed: {exc}", is_error=True)

    async def _list_tasks_handler(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
        gate = await _check_orchestration_gate(ctx)
        if isinstance(gate, ToolResult):
            return gate
        project_id, _agent_slug = gate
        try:
            tasks = await queries.list_tasks(
                project_id,
                status=args.get("status"),
                mine_session_id=ctx.session_id if args.get("mine_only") else None,
                limit=int(args.get("limit") or 20),
                user_id=ctx.user_id,
            )
            return ToolResult(content=json.dumps({"tasks": tasks}, ensure_ascii=False))
        except Exception as exc:  # noqa: BLE001
            logger.exception("list_tasks handler error in project %s", project_id)
            return ToolResult(content=f"list_tasks failed: {exc}", is_error=True)

    async def _get_task_handler(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
        gate = await _check_orchestration_gate(ctx)
        if isinstance(gate, ToolResult):
            return gate
        project_id, _agent_slug = gate
        task_id = (args.get("task_id") or "").strip()
        if not task_id:
            return ToolResult(content="get_task: task_id is required", is_error=True)
        try:
            detail = await queries.get_task(task_id, project_id, user_id=ctx.user_id)
            if detail is None:
                return ToolResult(
                    content=f"task {task_id!r} not found in this project", is_error=True
                )
            return ToolResult(content=json.dumps(detail, ensure_ascii=False))
        except Exception as exc:  # noqa: BLE001
            logger.exception("get_task handler error for %s", task_id)
            return ToolResult(content=f"get_task failed: {exc}", is_error=True)

    async def _list_members_handler(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
        # Read-only roster query — allowed for BOTH a task lead AND a plain
        # project-conversation launcher (so it can inspect the team before
        # create_task). NOT lead-gated; just needs a project. Resolve from
        # valuz metadata (task runs) or session.project_id (launcher).
        user_id = ctx.user_id
        sess = await data_reader().get_session(user_id, ctx.session_id)
        if sess is None:
            return ToolResult(content="list_members: caller session not found", is_error=True)
        v: dict[str, Any] = (sess.metadata or {}).get("valuz", {})
        project_id = v.get("project_id", "") or getattr(sess, "project_id", "")
        if not project_id:
            return ToolResult(content="list_members: caller session has no project", is_error=True)

        try:
            members = await queries.list_members(project_id, user_id=user_id)
            if not members:
                # Project-less chat fallback (see ``_bound_agent_member``):
                # a chat project has no deployed project members, but the
                # conversation IS driven by its bound agent. Surface it so the
                # roster isn't an empty dead-end that makes the caller give up
                # (e.g. abort an automation create) — the slug is usable
                # directly as the automation's agent_slug.
                bound = await _bound_agent_member(sess)
                if bound is not None:
                    members = [bound]
            return ToolResult(content=json.dumps(members, ensure_ascii=False))
        except Exception as exc:  # noqa: BLE001
            logger.exception("list_members handler error")
            return ToolResult(content=f"list_members failed: {exc}", is_error=True)

    async def _finish_task_handler(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
        gate = await _check_lead_gate(ctx)
        if isinstance(gate, ToolResult):
            return gate
        task_id, project_id = gate

        summary: str = args.get("summary", "")
        artifacts: list[str] = args.get("artifacts") or []
        status: str = args.get("status") or "completed"
        force: bool = bool(args.get("force") or False)

        try:
            result = await orchestrator.finish_task(
                task_id=task_id,
                project_id=project_id,
                lead_session_id=ctx.session_id,
                summary=summary,
                artifacts=artifacts,
                status=status,
                force=force,
                user_id=ctx.user_id,
            )
            # Plan-completeness guard rejected the close — surface it so the
            # lead dispatches the remaining subtasks instead of stopping.
            if isinstance(result, dict) and result.get("status") == "rejected":
                return ToolResult(
                    content=result.get("error", "finish_task rejected"), is_error=True
                )
            return ToolResult(content="Task closed. Events appended. Do not continue working.")
        except Exception as exc:  # noqa: BLE001
            logger.exception("finish_task handler error for task %s", task_id)
            return ToolResult(content=f"finish_task failed: {exc}", is_error=True)

    # -- plan / review handlers (VALUZ-TASK + VALUZ-CHATPLAN S2) -----------

    async def _plan_task_handler(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
        resolved = await _resolve_plan_writer_task(ctx, args)
        if isinstance(resolved, ToolResult):
            return resolved
        task, project_id, task_id = resolved

        # D10 belt-and-suspenders: once a task is committed (committed_at set
        # OR plan_pre_committed implicit by status=active), reject plan_task.
        # The committed brief tells the lead not to call this anyway.
        if task.committed_at is not None or task.status == "active":
            from valuz_agent.modules.tasks.plan import TaskPlan as _TaskPlan

            if not _TaskPlan.from_dict(task.plan).is_empty:
                return ToolResult(
                    content=(
                        "plan_task: this task already has a committed plan — "
                        "use modify_plan to change it (handler rejects re-planning "
                        "a non-empty committed plan)"
                    ),
                    is_error=True,
                )

        subtasks = args.get("subtasks") or []
        try:
            result = await planning.plan_task(
                task_id=task_id,
                project_id=project_id,
                lead_session_id=ctx.session_id,
                subtasks=subtasks,
                user_id=ctx.user_id,
            )
            return ToolResult(
                content=json.dumps(result, ensure_ascii=False), is_error="error" in result
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("plan_task handler error for task %s", task_id)
            return ToolResult(content=f"plan_task failed: {exc}", is_error=True)

    async def _get_plan_handler(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
        # get_plan is read-only: any project member can read.
        resolved = await _resolve_plan_reader_task(ctx, args)
        if isinstance(resolved, ToolResult):
            return resolved
        _task, project_id, task_id = resolved
        try:
            result = await planning.get_plan(
                task_id=task_id, project_id=project_id, user_id=ctx.user_id
            )
            return ToolResult(
                content=json.dumps(result, ensure_ascii=False), is_error="error" in result
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("get_plan handler error for task %s", task_id)
            return ToolResult(content=f"get_plan failed: {exc}", is_error=True)

    async def _modify_plan_handler(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
        resolved = await _resolve_plan_writer_task(ctx, args)
        if isinstance(resolved, ToolResult):
            return resolved
        _task, project_id, task_id = resolved
        expected_version_arg = args.get("expected_version")
        try:
            result = await planning.modify_plan(
                task_id=task_id,
                project_id=project_id,
                lead_session_id=ctx.session_id,
                add=args.get("add"),
                update=args.get("update"),
                expected_version=(
                    int(expected_version_arg) if expected_version_arg is not None else None
                ),
                user_id=ctx.user_id,
            )
            return ToolResult(
                content=json.dumps(result, ensure_ascii=False), is_error="error" in result
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("modify_plan handler error for task %s", task_id)
            return ToolResult(content=f"modify_plan failed: {exc}", is_error=True)

    async def _review_subtask_handler(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
        gate = await _check_lead_gate(ctx)
        if isinstance(gate, ToolResult):
            return gate
        task_id, project_id = gate
        decision = (args.get("decision") or "").strip()
        if decision not in ("approve", "rework"):
            return ToolResult(
                content="review_subtask: 'decision' must be 'approve' or 'rework'", is_error=True
            )
        if decision == "rework" and not (args.get("feedback") or "").strip():
            return ToolResult(
                content="review_subtask: 'feedback' is required when decision='rework'",
                is_error=True,
            )
        try:
            result = await planning.review_subtask(
                task_id=task_id,
                project_id=project_id,
                lead_session_id=ctx.session_id,
                decision=decision,
                subtask_key=args.get("subtask_key"),
                session_id=args.get("session_id"),
                feedback=args.get("feedback"),
                user_id=ctx.user_id,
            )
            return ToolResult(
                content=json.dumps(result, ensure_ascii=False), is_error="error" in result
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("review_subtask handler error for task %s", task_id)
            return ToolResult(content=f"review_subtask failed: {exc}", is_error=True)

    async def _stop_subtask_handler(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
        """Lead-only HARD stop of an in-flight subtask. Wraps the existing
        ``orchestrator.stop_member`` (which was reachable only from the user
        ``:intervene`` HTTP route) so the lead can cancel a member from inside
        its own turn."""
        user_id = ctx.user_id
        gate = await _check_lead_gate(ctx)
        if isinstance(gate, ToolResult):
            return gate
        task_id, project_id = gate

        # Resolve target session id from explicit arg or via subtask_key →
        # latest_run_session_id on the plan node.
        target_session_id = (args.get("session_id") or "").strip()
        subtask_key = (args.get("subtask_key") or "").strip()
        if not target_session_id and not subtask_key:
            return ToolResult(
                content="stop_subtask: either 'session_id' or 'subtask_key' is required",
                is_error=True,
            )

        if not target_session_id:
            # Look up by subtask_key
            from valuz_agent.infra.db import async_unit_of_work
            from valuz_agent.modules.tasks.datastore import TaskDatastore
            from valuz_agent.modules.tasks.plan import TaskPlan

            async with async_unit_of_work(commit=False) as db:
                task = await TaskDatastore(db).get_task_by_project(user_id, project_id, task_id)
            if task is None:
                return ToolResult(
                    content=f"stop_subtask: task {task_id!r} not found", is_error=True
                )
            node = TaskPlan.from_dict(task.plan).get(subtask_key)
            if node is None:
                return ToolResult(
                    content=f"stop_subtask: no subtask with key {subtask_key!r}",
                    is_error=True,
                )
            target_session_id = node.latest_run_session_id or ""
            if not target_session_id:
                return ToolResult(
                    content=(
                        f"stop_subtask: subtask {subtask_key!r} has no in-flight run "
                        "to stop (latest_run_session_id is null)"
                    ),
                    is_error=True,
                )

        reason = (args.get("reason") or "").strip()
        try:
            ok = await orchestrator.stop_member(target_session_id, user_id=user_id)
            if not ok:
                return ToolResult(
                    content=(
                        f"stop_subtask: member session {target_session_id!r} not found "
                        "or is not a subtask (already finished?)"
                    ),
                    is_error=True,
                )
            return ToolResult(
                content=json.dumps(
                    {
                        "stopped": True,
                        "session_id": target_session_id,
                        "subtask_key": subtask_key or None,
                        "reason": reason,
                        "next": (
                            "plan node is now `rework`; call dispatch(key) to retry "
                            "with a corrected goal, or modify_plan to retire it"
                        ),
                    },
                    ensure_ascii=False,
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("stop_subtask handler error for task %s", task_id)
            return ToolResult(content=f"stop_subtask failed: {exc}", is_error=True)

    # Register with live handlers
    register_tool(
        ToolDef(
            name=DISPATCH_TOOL_NAME,
            description=DISPATCH_TOOL_DECLARATION.description,
            parameters=_DISPATCH_PARAMETERS,
            handler=_dispatch_handler,
        )
    )
    register_tool(
        ToolDef(
            name=AWAIT_MEMBERS_TOOL_NAME,
            description=AWAIT_MEMBERS_TOOL_DECLARATION.description,
            parameters=_AWAIT_MEMBERS_PARAMETERS,
            handler=_await_members_handler,
        )
    )
    register_tool(
        ToolDef(
            name=LIST_MEMBERS_TOOL_NAME,
            description=LIST_MEMBERS_TOOL_DECLARATION.description,
            parameters=_LIST_MEMBERS_PARAMETERS,
            handler=_list_members_handler,
        )
    )
    register_tool(
        ToolDef(
            name=FINISH_TASK_TOOL_NAME,
            description=FINISH_TASK_TOOL_DECLARATION.description,
            parameters=_FINISH_TASK_PARAMETERS,
            handler=_finish_task_handler,
        )
    )
    register_tool(
        ToolDef(
            name=SEND_TOOL_NAME,
            description=SEND_TOOL_DECLARATION.description,
            parameters=_SEND_PARAMETERS,
            handler=_send_handler,
        )
    )
    register_tool(
        ToolDef(
            name=CREATE_TASK_TOOL_NAME,
            description=CREATE_TASK_TOOL_DECLARATION.description,
            parameters=_CREATE_TASK_PARAMETERS,
            handler=_create_task_handler,
        )
    )
    register_tool(
        ToolDef(
            name=LIST_TASKS_TOOL_NAME,
            description=LIST_TASKS_TOOL_DECLARATION.description,
            parameters=_LIST_TASKS_PARAMETERS,
            handler=_list_tasks_handler,
        )
    )
    register_tool(
        ToolDef(
            name=GET_TASK_TOOL_NAME,
            description=GET_TASK_TOOL_DECLARATION.description,
            parameters=_GET_TASK_PARAMETERS,
            handler=_get_task_handler,
        )
    )
    register_tool(
        ToolDef(
            name=PLAN_TASK_TOOL_NAME,
            description=PLAN_TASK_TOOL_DECLARATION.description,
            parameters=_PLAN_TASK_PARAMETERS,
            handler=_plan_task_handler,
        )
    )
    register_tool(
        ToolDef(
            name=GET_PLAN_TOOL_NAME,
            description=GET_PLAN_TOOL_DECLARATION.description,
            parameters=_GET_PLAN_PARAMETERS,
            handler=_get_plan_handler,
            read_only=True,
        )
    )
    register_tool(
        ToolDef(
            name=MODIFY_PLAN_TOOL_NAME,
            description=MODIFY_PLAN_TOOL_DECLARATION.description,
            parameters=_MODIFY_PLAN_PARAMETERS,
            handler=_modify_plan_handler,
        )
    )
    register_tool(
        ToolDef(
            name=REVIEW_SUBTASK_TOOL_NAME,
            description=REVIEW_SUBTASK_TOOL_DECLARATION.description,
            parameters=_REVIEW_SUBTASK_PARAMETERS,
            handler=_review_subtask_handler,
        )
    )
    # VALUZ-CHATPLAN S2 — state-transition tools
    register_tool(
        ToolDef(
            name=DRAFT_TASK_TOOL_NAME,
            description=DRAFT_TASK_TOOL_DECLARATION.description,
            parameters=_DRAFT_TASK_PARAMETERS,
            handler=_draft_task_handler,
        )
    )
    register_tool(
        ToolDef(
            name=COMMIT_TASK_TOOL_NAME,
            description=COMMIT_TASK_TOOL_DECLARATION.description,
            parameters=_COMMIT_TASK_PARAMETERS,
            handler=_commit_task_handler,
        )
    )
    register_tool(
        ToolDef(
            name=ABANDON_TASK_TOOL_NAME,
            description=ABANDON_TASK_TOOL_DECLARATION.description,
            parameters=_ABANDON_TASK_PARAMETERS,
            handler=_abandon_task_handler,
        )
    )
    register_tool(
        ToolDef(
            name=INJECT_INTO_TASK_TOOL_NAME,
            description=INJECT_INTO_TASK_TOOL_DECLARATION.description,
            parameters=_INJECT_INTO_TASK_PARAMETERS,
            handler=_inject_into_task_handler,
        )
    )
    register_tool(
        ToolDef(
            name=RESUME_TASK_TOOL_NAME,
            description=RESUME_TASK_TOOL_DECLARATION.description,
            parameters=_RESUME_TASK_PARAMETERS,
            handler=_resume_task_handler,
        )
    )
    register_tool(
        ToolDef(
            name=STOP_SUBTASK_TOOL_NAME,
            description=STOP_SUBTASK_TOOL_DECLARATION.description,
            parameters=_STOP_SUBTASK_PARAMETERS,
            handler=_stop_subtask_handler,
        )
    )

    async def _update_deliverable_handler(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
        user_id = ctx.user_id
        gate = await _check_lead_gate(ctx)
        if isinstance(gate, ToolResult):
            return gate
        task_id, project_id = gate

        summary: str = args.get("summary", "")
        artifacts: list[str] = args.get("artifacts") or []

        try:
            result = await orchestrator.update_deliverable(
                task_id=task_id,
                project_id=project_id,
                lead_session_id=ctx.session_id,
                summary=summary,
                artifacts=artifacts,
                user_id=user_id,
            )
            if isinstance(result, dict) and result.get("status") == "rejected":
                return ToolResult(
                    content=result.get("error", "update_deliverable rejected"),
                    is_error=True,
                )
            return ToolResult(content="Deliverable card refreshed.")
        except Exception as exc:  # noqa: BLE001
            logger.exception("update_deliverable handler error for task %s", task_id)
            return ToolResult(content=f"update_deliverable failed: {exc}", is_error=True)

    register_tool(
        ToolDef(
            name=UPDATE_DELIVERABLE_TOOL_NAME,
            description=UPDATE_DELIVERABLE_TOOL_DECLARATION.description,
            parameters=_UPDATE_DELIVERABLE_PARAMETERS,
            handler=_update_deliverable_handler,
        )
    )
    logger.info(
        "Built task tool defs: %s",
        ", ".join(sorted(t.name for t in _defs)),
    )
    return tuple(_defs)
