"""ActorRunner — the single session-turn-to-idle / actor-loop primitive.

Runtime layer (ADR-023). Owns the agent-turn engine that drives a kernel
session through one or more turns:

  * :class:`ActorRunner` — the persistent v2 actor loop (``run_actor_loop``)
    plus its per-turn primitive (``run_turn``) and the member_done prompt
    renderer (``_format_member_done``).
  * :func:`collect_manifest` — pure manifest builder used by dispatcher /
    coordination / recovery.
  * :func:`_member_run_dir` — resolve a member's working dir by isolation mode.

The one-shot chat-path driver (``run_session_to_idle``) lives in
``sessions/turn_driver.py``; the shared turn semantics
(``_resolve_turn_status`` / ``_restamp_always_on_mcp``) are imported from
there so both drivers read one implementation.

Collaborators
-------------
Running a turn is the runner's OWN job (:meth:`ActorRunner.run_turn`). What
happens *around* a turn is not, so the loop delegates it through two narrow,
typed protocols bound at the composition root:

  * :class:`ActorFinalizer`   — what to do when the loop exits
    (``LifecycleService``).
  * :class:`ActorCoordinator` — the two role-specific between-turn questions
    (``CoordinationService``).

Both are bound late, via :meth:`ActorRunner.bind`, because the runner is
constructed *before* the services that depend on it. Protocols rather than an
untyped handle: the seam is the heart of the task system, and mypy verifying
that the concrete services still satisfy it is worth more than the flexibility
of duck typing — an earlier ``host: Any`` version let three delegators rot out
of signature sync unnoticed.
"""

# ruff: noqa: I001
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Literal, Protocol

from valuz_agent.adapters import kernel_client
from valuz_agent.adapters.data_reader import data_reader
from valuz_agent.infra.lifecycle import is_draining
from valuz_agent.modules.tasks.mailbox import InboxMsg, mailbox_registry
from valuz_agent.modules.sessions.turn_driver import (
    _restamp_always_on_mcp,
    _resolve_turn_status,
)

logger = logging.getLogger(__name__)


# ``member_done`` payload statuses that carry NO reviewable deliverable — the
# member died (terminated/error) or was cancelled by the user/stop_member
# (cancelled/interrupted). Consumers must NOT flip the plan node to
# ``in_review`` for these: the node is already parked in ``rework`` and
# presenting a dead run as a pending deliverable confuses the lead.
_NON_REVIEWABLE_DONE = frozenset({"terminated", "error", "cancelled", "interrupted"})




# ---------------------------------------------------------------------------
# collect_manifest
# ---------------------------------------------------------------------------


# Skip these directory names when scanning a (possibly project-root) cwd for
# artifacts — they are noise, not member output.
_ARTIFACT_SKIP_DIRS = frozenset({"node_modules", "__pycache__", "dist", "build", ".venv"})
# Cap on artifacts listed in a manifest (shared project cwd can be large).
_ARTIFACT_LIMIT = 200


def _scan_artifacts(run_dir: Path, since_epoch: float) -> list[dict[str, Any]]:
    """List up to ``_ARTIFACT_LIMIT`` files under *run_dir* touched since
    *since_epoch*, in sorted path order. BLOCKING — always call via
    ``asyncio.to_thread`` (``run_dir`` is usually the whole project cwd).
    """
    artifacts: list[dict[str, Any]] = []
    if not run_dir.exists():
        return artifacts
    for fpath in sorted(run_dir.rglob("*")):
        if len(artifacts) >= _ARTIFACT_LIMIT:
            break
        # Skip hidden parts (.claude/, .git/) and known noise dirs.
        if any(p.startswith(".") for p in fpath.parts):
            continue
        if any(p in _ARTIFACT_SKIP_DIRS for p in fpath.parts):
            continue
        if not fpath.is_file():
            continue
        try:
            st = fpath.stat()
            # Attribute by mtime: under the shared project cwd this keeps only
            # what the member touched during its run (approximate — see M10
            # 附录 D.2). since_epoch=0 → include all.
            if st.st_mtime < since_epoch:
                continue
            artifacts.append({"path": str(fpath), "size": st.st_size})
        except OSError:
            pass
    return artifacts


async def collect_manifest(
    session_id: str,
    run_dir: Path,
    status: str,
    *,
    since_epoch: float = 0.0,
    user_id: str,
) -> dict[str, Any]:
    """Build a SubtaskResult manifest after a member session completes.

    summary    — text of the last assistant message (best-effort)
    artifacts  — list of {path, size} for files under run_dir written by this
                 member. Under v2.1 the member's cwd is the shared project dir,
                 so we attribute artifacts by mtime ≥ *since_epoch* (the
                 dispatch-start time) instead of relying on a private run dir.
                 ``since_epoch=0.0`` means "include everything" (worktree /
                 legacy private dir, where every file is the member's).
    status     — the final session status string
    session_id — for cross-reference
    """
    # Extract summary from the last assistant event
    summary = ""
    try:
        events = await kernel_client.get_events(user_id, session_id, limit=200)
        # Walk backwards: find last assistant_message text
        for event in reversed(events):
            payload = event.data if hasattr(event, "data") else {}
            if event.type in ("assistant_message", "text_delta", "content_block"):
                text = payload.get("text") or payload.get("content") or ""
                if text:
                    summary = str(text)[:2000]  # cap at 2k chars
                    break
    except Exception:  # noqa: BLE001
        logger.debug("collect_manifest: could not extract summary for %s", session_id)

    # Scan run_dir for artifact files written during this member's run.
    # Offloaded: under v2.1 ``run_dir`` is the whole shared project cwd, so this
    # walks an arbitrarily large tree with blocking ``stat`` calls. On the event
    # loop that stalled EVERY other session for the duration.
    try:
        artifacts = await asyncio.to_thread(_scan_artifacts, run_dir, since_epoch)
    except Exception:  # noqa: BLE001
        logger.debug("collect_manifest: artifact scan failed for %s", run_dir)
        artifacts = []

    return {
        "session_id": session_id,
        "status": status,
        "summary": summary,
        "artifacts": artifacts,
    }


def _member_run_dir(project_cwd: str | Path, task_id: str, run_seq: int, mode: str) -> Path:
    """Resolve a member's working directory (v2.1: always the project cwd).

    Members read and write project files natively in the SHARED project cwd
    (task-level worktrees relocate that cwd wholesale — see task_worktree.py);
    the legacy per-member ``repo-worktree`` isolation mode is retired. ``mode``
    is still recorded on the run row for display.
    """
    return Path(project_cwd)


# ---------------------------------------------------------------------------
# v2 actor-loop tuning (M10 附录 B)
# ---------------------------------------------------------------------------

# Max turns a single actor (lead or member) will run before self-reaping, as a
# runaway guard. Leads make many turns across dispatches; members fewer.
ACTOR_MAX_TURNS = 60
# Idle TTL: how long an actor waits on its mailbox between turns before giving
# up and finalising. Lead waits longer (members may run a while); a member that
# the lead never follows up on self-reaps sooner.
LEAD_IDLE_TTL_S = 1800.0
MEMBER_IDLE_TTL_S = 600.0

# How many times an idle-TTL expiry may be EXTENDED because the session turned
# out to still be working (see the ``session_still_working`` probe in
# ``run_actor_loop``). Bounded so a session wedged in ``running`` forever cannot
# pin an actor loop for the life of the process; generous enough that real
# background work — which routinely outlasts one TTL — is never reaped.
MAX_IDLE_EXTENSIONS = 6


# ---------------------------------------------------------------------------
# Collaborator protocols
# ---------------------------------------------------------------------------


class ActorFinalizer(Protocol):
    """What the actor loop calls once, when it exits. (``LifecycleService``.)"""

    async def finalize_actor(
        self,
        *,
        session_id: str,
        last_content: str,
        final_status: str,
        role: Literal["lead", "subtask"],
        task_id: str,
        project_id: str,
        via_shutdown: bool = False,
        user_id: str,
    ) -> None: ...


class ActorCoordinator(Protocol):
    """The two role-specific between-turn questions. (``CoordinationService``.)"""

    async def notify_lead_member_idle(
        self, session_id: str, status: str, user_id: str
    ) -> None:
        """A member finished a turn — post ``member_done`` to its lead's inbox."""
        ...

    async def lead_idle_with_no_pending(
        self, task_id: str, project_id: str, user_id: str, lead_session_id: str = ""
    ) -> bool:
        """True when a lead has nothing left to wait for and should stop looping."""
        ...

    async def session_still_working(self, session_id: str) -> bool:
        """True when the session is doing work THIS loop cannot see.

        Chiefly a ``run_in_background`` subagent: the launching turn ended, so
        the loop is parked on its mailbox, but the CLI keeps driving follow-up
        turns on the session and the work is very much alive.
        """
        ...


# ---------------------------------------------------------------------------
# ActorRunner
# ---------------------------------------------------------------------------


class ActorRunner:
    """The persistent v2 actor loop + its per-turn primitive.

    Constructed once at the composition root and shared by the dispatcher,
    coordination, lifecycle and recovery services. Holds NO task state: running
    a turn is its own job (:meth:`run_turn`), and everything around a turn goes
    through the two injected protocols.

    Collaborators are bound after construction via :meth:`bind` — the root must
    build the runner first (the services take it as a constructor argument),
    then the services, then bind them back.
    """

    def __init__(
        self,
        *,
        finalizer: ActorFinalizer | None = None,
        coordinator: ActorCoordinator | None = None,
    ) -> None:
        self._finalizer = finalizer
        self._coordinator = coordinator

    def bind(self, *, finalizer: ActorFinalizer, coordinator: ActorCoordinator) -> None:
        """Bind the collaborators the loop delegates its around-a-turn work to."""
        self._finalizer = finalizer
        self._coordinator = coordinator

    async def run_turn(self, session_id: str, content: str, user_id: str) -> str:
        """Run ONE turn on a persistent session and return its final status.

        Unlike :func:`run_session_to_idle`, this does NOT finalize or clean up
        the session — the actor loop owns that, once, at loop exit. Live
        events reach SSE followers through the kernel's bus taps.
        """
        # Heal a stale in-process MCP token before every actor-loop turn — this
        # is the path a recovered / resumed lead+member loop runs on after a
        # backend restart, where the persisted ``harness`` token is stale and
        # would otherwise 403 (hiding dispatch / review_subtask / finish_task).
        await _restamp_always_on_mcp(session_id, user_id)
        try:
            # Classify off the AUTHORITATIVE run_turn result, not a re-read of
            # the lagging durable session (see ``_resolve_turn_status``). The
            # kernel persists ``status="running"`` at turn start itself
            # (agent-harness 3e742fc) — no host pre-persist needed.
            message = await kernel_client.run_turn(user_id, session_id, content)
            return _resolve_turn_status(message)
        except Exception as exc:  # noqa: BLE001
            logger.warning("actor turn failed for session %s: %s", session_id, exc)
            # A user interrupt can also surface as a raised exception (the SDK
            # tears the turn down) — there is NO ``message`` on this path, so the
            # session re-read is the only signal available: if the kernel stamped
            # a cancellation stop_reason, this is intent, not a failure.
            try:
                loaded = await data_reader().get_session(user_id, session_id)
                if _resolve_turn_status(loaded) == "interrupted":
                    return "interrupted"
            except Exception:  # noqa: BLE001
                pass
            return "terminated"

    async def run_actor_loop(
        self,
        *,
        session_id: str,
        initial_prompt: str,
        role: Literal["lead", "subtask"],
        task_id: str,
        project_id: str,
        idle_ttl: float | None = None,
        user_id: str,
    ) -> None:
        """Persistent actor loop: run turn → idle → await mailbox → repeat.

        Replaces the one-shot ``run_session_to_idle`` for v2 sessions. The loop
        exits on shutdown message, idle-TTL expiry, max-turns, or a terminal
        turn status, then finalizes the session exactly once.
        """
        from valuz_agent.modules.tasks import planning

        if self._finalizer is None or self._coordinator is None:
            raise RuntimeError(
                "ActorRunner.run_actor_loop: collaborators not bound — the "
                "composition root must call bind(finalizer=..., coordinator=...) "
                "before any actor loop starts"
            )
        finalizer, coordinator = self._finalizer, self._coordinator
        ttl = (
            idle_ttl
            if idle_ttl is not None
            else (LEAD_IDLE_TTL_S if role == "lead" else MEMBER_IDLE_TTL_S)
        )
        mailbox_registry.register(session_id)
        prompt = initial_prompt
        final_status = "idle"
        turns = 0
        # Did the loop exit because of a ``shutdown`` mailbox message (pause /
        # stop / finish_task broadcast)? Those exits are externally-managed —
        # the task status is owned by stop_task / finish_task — so the lead's
        # ``_auto_finalize`` MUST be skipped, else a rapid pause→resume races:
        # the old loop's finalize runs after resume flips the task back to
        # ``active`` and wrongly blocks it (VALUZ pause/resume).
        exited_on_shutdown = False
        extensions = 0
        try:
            while True:
                # App is shutting down — do NOT start a new turn (it would spawn
                # a runtime against a process being torn down, e.g. a fresh codex
                # turn that immediately hits a dead pipe). Break and let the
                # ``finally`` leave the session for boot recovery.
                if is_draining():
                    exited_on_shutdown = True
                    break
                final_status = await self.run_turn(session_id, prompt, user_id=user_id)
                turns += 1

                # A member notifies its lead after every idle (carries manifest).
                # Skip it for a user-interrupted turn: ``_finalize_actor`` owns
                # that path and delivers exactly one ``member_done(cancelled)``
                # (or none, when ``stop_member`` already notified the lead) —
                # notifying here too would double-deliver.
                if role == "subtask" and final_status != "interrupted":
                    await coordinator.notify_lead_member_idle(
                        session_id, final_status, user_id=user_id
                    )

                if final_status in ("terminated", "error", "interrupted"):
                    break
                if turns >= ACTOR_MAX_TURNS:
                    logger.warning(
                        "actor loop %s (%s) hit ACTOR_MAX_TURNS=%s",
                        session_id,
                        role,
                        ACTOR_MAX_TURNS,
                    )
                    break

                # Lead with nothing outstanding → finalize NOW, don't idle for
                # LEAD_IDLE_TTL_S (30min) waiting for a member_done that will
                # never come. A lead only has reason to wait when it has a queued
                # message, a member in flight, OR an unresolved plan node still
                # to drive. Without this, a lead that satisfies the goal inline
                # (no dispatch — e.g. "你好" / a simple news query) sits "active"
                # for 30 minutes before the idle-TTL fires _finalize_actor.
                # NB: must check the mailbox is empty first, else a queued
                # follow-up / member_done would be dropped.
                if (
                    role == "lead"
                    and not mailbox_registry.has_pending(session_id)
                    and await coordinator.lead_idle_with_no_pending(
                        task_id, project_id, user_id=user_id, lead_session_id=session_id
                    )
                ):
                    logger.info(
                        "actor loop %s (lead) idle with no in-flight members / unresolved "
                        "plan — finalizing immediately",
                        session_id,
                    )
                    break

                try:
                    msg = await mailbox_registry.get(session_id, timeout=ttl)
                except TimeoutError:
                    # The TTL measures silence on OUR mailbox, which is not the
                    # same as the session being idle. A ``run_in_background``
                    # subagent keeps working after the turn that launched it,
                    # and the CLI drives the follow-up turns itself — the loop
                    # sees none of that and its clock never resets. A real task
                    # was reaped exactly this way: the lead spawned two
                    # subagents, the session ran two more turns while the loop
                    # sat parked, and 1800s after the FIRST turn the task was
                    # closed as ``blocked`` while the work was still running.
                    #
                    # So ask before concluding. Bounded by MAX_IDLE_EXTENSIONS
                    # so a session wedged ``running`` cannot pin the loop.
                    if extensions < MAX_IDLE_EXTENSIONS and await coordinator.session_still_working(
                        session_id
                    ):
                        extensions += 1
                        logger.info(
                            "actor loop %s (%s) idle-TTL expired but the session is "
                            "still working (background task) — extending (%d/%d)",
                            session_id,
                            role,
                            extensions,
                            MAX_IDLE_EXTENSIONS,
                        )
                        continue
                    logger.info("actor loop %s (%s) idle-TTL expired", session_id, role)
                    break

                if msg.kind == "shutdown":
                    exited_on_shutdown = True
                    break
                if msg.kind == "member_done":
                    # Lead-side, single-actor (D7): flip the member's plan node
                    # to in_review so the lead reviews it (member-idle ≠ done).
                    # ONLY for a delivering member: a failed/cancelled
                    # member_done has no work to review — its node was already
                    # parked in ``rework`` by finalize / stop_member, and
                    # flipping it back to in_review would present a dead run as
                    # a pending deliverable.
                    done_status = str((msg.payload or {}).get("status") or "")
                    if (
                        role == "lead"
                        and msg.from_session
                        and done_status not in _NON_REVIEWABLE_DONE
                    ):
                        await planning.mark_in_review(
                            task_id=task_id,
                            project_id=project_id,
                            member_session_id=msg.from_session,
                            user_id=user_id,
                        )
                    prompt = self._format_member_done(msg)
                else:  # "text" / "revise_goal" — authoritative text → next turn
                    prompt = msg.text
        finally:
            mailbox_registry.unregister(session_id)
            # When draining, skip the ENTIRE finalize. ``_finalize_actor`` touches
            # the kernel store (status flip) AND the host DB (lead auto-finalize /
            # member run record), both being torn down right now; running it spams
            # errors and would mark the task/member terminal — the opposite of what
            # boot recovery wants. Leave the session ``running`` / the task
            # ``active``; recovery resumes it. (A plain ``if`` — never ``return``
            # from a ``finally``, which would swallow a propagating CancelledError.)
            if not is_draining():
                await finalizer.finalize_actor(
                    session_id=session_id,
                    last_content=prompt,
                    final_status=final_status,
                    role=role,
                    task_id=task_id,
                    project_id=project_id,
                    via_shutdown=exited_on_shutdown,
                    user_id=user_id,
                )

    @staticmethod
    def _format_member_done(msg: InboxMsg) -> str:
        """Render a member_done mailbox message as the lead's next turn prompt."""
        m = msg.payload or {}
        arts = m.get("artifacts") or []
        art_lines = "\n".join(f"- {a.get('path')}" for a in arts) if arts else "(none)"
        status = str(m.get("status", "") or "")
        if status in _NON_REVIEWABLE_DONE:
            # No deliverable to review — the run died or was cancelled. The
            # node is already parked in ``rework``; guide the lead toward a
            # decision instead of a review of nothing.
            guidance = (
                "The member above did NOT deliver — its run "
                f"ended with status '{status}' and its plan node is now in "
                "'rework'. There is nothing to review. Decide next: re-dispatch "
                "the subtask (dispatch + await_members), adjust the plan "
                "(modify_plan), or — if the user cancelled it on purpose and the "
                "goal is unreachable without it — finish_task(status='stopped')."
            )
        else:
            guidance = (
                "The member above went idle. Review its result (review_subtask), "
                "then either send it a follow-up (send), dispatch more work "
                "(dispatch + await_members), or call finish_task if the overall "
                "goal is met."
            )
        return (
            f'<member-result agent="{m.get("agent", "")}" '
            f'session="{msg.from_session}" status="{status}">\n'
            f"{m.get('summary', '')}\n\n"
            f"Artifacts:\n{art_lines}\n"
            f"</member-result>\n\n" + guidance
        )


__all__ = [
    "ActorCoordinator",
    "ActorFinalizer",
    "ActorRunner",
    "collect_manifest",
    "_member_run_dir",
    "ACTOR_MAX_TURNS",
    "LEAD_IDLE_TTL_S",
    "MAX_IDLE_EXTENSIONS",
    "MEMBER_IDLE_TTL_S",
    "_NON_REVIEWABLE_DONE",
]
