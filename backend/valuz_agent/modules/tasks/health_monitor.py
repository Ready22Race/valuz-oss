"""Live watchdog for in-flight tasks (task attention & reliability, P2).

Boot recovery (``recover_active_tasks``) only reconciles ``active`` tasks at
process startup — it cannot see a lead that silently dies while the process
stays up (an uncaught crash in the actor loop, or a ``_finalize_actor`` that
itself failed). Such a task sits ``active`` forever: the timeline shows
"Running", nothing is dispatched, and the user has no signal. Automations
already have a background monitor (ADR-012 ``failure_monitor``); tasks did not.

``TaskHealthMonitor`` closes that hole with a periodic sweep of every ``active``
task. The liveness signal is the lead's mailbox registration: the actor loop
registers the lead session on entry (``run_actor_loop`` → ``mailbox_registry
.register``) and unregisters it in a ``finally`` on exit. So a lead session that
is **not** registered has no live loop — either it never started (a spawn we
just missed) or it exited without finalizing the task. To avoid acting on the
brief spawn/resume window, a task must look dead for ``confirm_sweeps``
consecutive sweeps before we intervene.

Intervention is deliberately minimal: flip the task ``active → blocked`` and
emit ``task_blocked(reason="lead_dead")``. ``blocked`` is the single "needs
intervention" terminal — it surfaces the failure (banner + attention dot) and
is already user-resumable via ``resume_task`` (which rebuilds a fresh lead). The
monitor never respawns or mutates the plan itself; recovery stays the user's (or
a future auto-resume policy's) call.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import timedelta

from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.infra.lifecycle import is_draining
from valuz_agent.modules.tasks.datastore import (
    TaskDatastore,
    TaskEventDatastore,
    TaskSessionDatastore,
)
from valuz_agent.modules.tasks.mailbox import mailbox_registry

logger = logging.getLogger(__name__)


def _parse_duration_env(name: str, default: timedelta) -> timedelta:
    """``"30"`` → 30s; ``"5m"`` / ``"90s"`` / ``"1h"`` → that duration.
    Bad input warns and returns the default. Mirrors the automations monitor."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    suffixes: dict[str, int] = {"d": 86400, "h": 3600, "m": 60, "s": 1}
    try:
        if raw[-1] in suffixes:
            return timedelta(seconds=int(raw[:-1]) * suffixes[raw[-1]])
        return timedelta(seconds=int(raw))
    except (ValueError, IndexError):
        logger.warning("task health monitor: bad duration %s=%r, using default", name, raw)
        return default


@dataclass(frozen=True)
class TaskHealthConfig:
    interval: timedelta = timedelta(seconds=60)
    startup_delay: timedelta = timedelta(seconds=90)
    # A task must look dead for this many consecutive sweeps before we act —
    # absorbs the brief spawn/resume window where the loop hasn't registered
    # its mailbox yet.
    confirm_sweeps: int = 2

    @property
    def enabled(self) -> bool:
        return self.interval.total_seconds() > 0

    @classmethod
    def from_env(cls) -> TaskHealthConfig:
        return cls(
            interval=_parse_duration_env(
                "VALUZ_TASK_HEALTH_MONITOR_INTERVAL", cls.interval
            ),
            startup_delay=_parse_duration_env(
                "VALUZ_TASK_HEALTH_MONITOR_STARTUP_DELAY", cls.startup_delay
            ),
        )


class TaskHealthMonitor:
    def __init__(self, config: TaskHealthConfig | None = None) -> None:
        self._config = config or TaskHealthConfig.from_env()
        self._running = False
        self._task: asyncio.Task[None] | None = None
        # task_id → consecutive dead-looking sweep count.
        self._suspect: dict[str, int] = {}

    async def startup(self) -> None:
        if not self._config.enabled:
            logger.info("task health monitor: disabled (interval<=0)")
            return
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._tick_loop())
        logger.info(
            "task health monitor: started (interval=%s, startup_delay=%s, confirm_sweeps=%d)",
            self._config.interval,
            self._config.startup_delay,
            self._config.confirm_sweeps,
        )

    async def shutdown(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        self._suspect.clear()
        logger.info("task health monitor: stopped")

    async def _tick_loop(self) -> None:
        if self._config.startup_delay.total_seconds() > 0:
            try:
                await asyncio.sleep(self._config.startup_delay.total_seconds())
            except asyncio.CancelledError:
                return
        interval_s = self._config.interval.total_seconds()
        while self._running:
            await self._safe_sweep()
            try:
                await asyncio.sleep(interval_s)
            except asyncio.CancelledError:
                break

    async def _safe_sweep(self) -> None:
        try:
            await self.sweep_once()
        except Exception:  # noqa: BLE001
            logger.exception("task health monitor: sweep failed")

    async def sweep_once(self) -> list[str]:
        """One pass over active tasks. Returns the task_ids marked blocked this
        sweep (for tests / observability). Never raises to the caller loop."""
        if is_draining():
            return []
        async with async_unit_of_work(commit=False) as db:
            tasks = await TaskDatastore(db).list_active()
            run_ds = TaskSessionDatastore(db)
            # Snapshot (task_id, user_id, project_id, lead_session_id) so we
            # don't hold the read UoW across the write below.
            candidates: list[tuple[str, str, str, str | None]] = []
            for task in tasks:
                runs = await run_ds.list_runs(task.user_id, task.id)
                lead = next((r for r in runs if r.kind == "lead"), None)
                candidates.append(
                    (task.id, task.user_id, task.project_id, lead.session_id if lead else None)
                )

        acted: list[str] = []
        live_task_ids: set[str] = set()
        for task_id, user_id, project_id, lead_session_id in candidates:
            live_task_ids.add(task_id)
            if lead_session_id is None:
                # No lead run at all — nothing this monitor can safely do; leave
                # it to boot recovery / user action.
                self._suspect.pop(task_id, None)
                continue
            if mailbox_registry.is_registered(lead_session_id):
                # Live lead loop (running a turn, or parked on its mailbox
                # awaiting member_done / a user question) — healthy.
                self._suspect.pop(task_id, None)
                continue
            # Dead-looking: the loop has exited but the task is still active.
            n = self._suspect.get(task_id, 0) + 1
            self._suspect[task_id] = n
            if n < self._config.confirm_sweeps:
                logger.debug(
                    "task health monitor: task %s lead loop absent (%d/%d) — waiting to confirm",
                    task_id,
                    n,
                    self._config.confirm_sweeps,
                )
                continue
            # Confirmed zombie across ``confirm_sweeps`` — mark blocked so it
            # surfaces + becomes user-resumable.
            self._suspect.pop(task_id, None)
            marked = await self._mark_blocked(task_id, user_id, project_id, lead_session_id)
            if marked:
                acted.append(task_id)

        # Drop suspicion for tasks that are no longer active (finished / paused
        # between sweeps) so the map doesn't grow unbounded.
        for stale in [tid for tid in self._suspect if tid not in live_task_ids]:
            self._suspect.pop(stale, None)
        return acted

    async def _mark_blocked(
        self, task_id: str, user_id: str, project_id: str, lead_session_id: str
    ) -> bool:
        async with async_unit_of_work() as db:
            task_ds = TaskDatastore(db)
            event_ds = TaskEventDatastore(db)
            # Re-read under the write UoW — the task may have moved off
            # ``active`` since the read snapshot (a late finalize won the race).
            task = await task_ds.get_task_by_project(user_id, project_id, task_id)
            if task is None or task.status != "active":
                return False
            # Double-check liveness right before writing — the loop may have
            # re-registered (a resume landed) in the sweep gap.
            if mailbox_registry.is_registered(lead_session_id):
                return False
            await task_ds.update_task_status(user_id, task_id, "blocked")
            reason = (
                "The lead stopped without finishing the task (the process "
                "stayed up but its loop exited). Resume to rebuild the lead "
                "and continue."
            )
            blocked_ev = await event_ds.append_event(
                user_id,
                project_id=project_id,
                task_id=task_id,
                type="task_blocked",
                actor=lead_session_id,
                session_id=lead_session_id,
                payload={"reason": "lead_dead", "error": reason},
            )
            from valuz_agent.modules.tasks import messaging as _msg

            await _msg.record_task_failure_notification(
                task_id=task_id,
                project_id=project_id,
                event_id=blocked_ev.id,
                event_type="task_blocked",
                reason=reason,
                user_id=user_id,
            )
        logger.warning(
            "task health monitor: task %s -> blocked (lead loop dead, session %s)",
            task_id,
            lead_session_id,
        )
        return True


# Process-singleton, mirrors ``automation_failure_monitor``.
task_health_monitor = TaskHealthMonitor()
