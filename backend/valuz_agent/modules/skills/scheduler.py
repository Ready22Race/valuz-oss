"""Background skill-library auto-scan — periodic rescan + re-index.

Runs as a daemon thread alongside the FastAPI process: re-discovers every skill
on disk (user / official / project) and refreshes ``valuz_skill_index`` every
interval, so skills added out-of-band (a team import, a dropped folder) become
resolvable without a restart. Shares ``SkillLibraryService.startup_scan`` with
the boot scan and the manual ``POST /v1/skills/scan`` endpoint.

Interval is ``VALUZ_SKILL_SCAN_INTERVAL_SEC`` (default 30 min); ``<= 0`` disables
the scheduler entirely.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

_DEFAULT_INTERVAL_SEC = 30 * 60  # 30 minutes


def _interval_sec() -> int:
    try:
        return int(os.environ.get("VALUZ_SKILL_SCAN_INTERVAL_SEC", _DEFAULT_INTERVAL_SEC))
    except ValueError:
        return _DEFAULT_INTERVAL_SEC


class SkillAutoScanScheduler:
    def __init__(self, scan_factory: Callable[[], None], interval: int) -> None:
        self._scan_factory = scan_factory
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="skill-auto-scan",
            daemon=True,
        )
        self._thread.start()
        logger.info("skill auto-scan scheduler started (interval=%ds)", self._interval)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("skill auto-scan scheduler stopped")

    def _loop(self) -> None:
        # Scan once on start, then every interval — matching the docs
        # auto-discovery scheduler so the task is observable immediately
        # (logs a pass at boot) rather than staying silent until the first
        # 30-min tick. The boot step also scanned, but ``startup_scan`` is
        # idempotent and cheap (~tens of ms), so the extra pass is harmless.
        self._run_once()
        while not self._stop.wait(timeout=self._interval):
            self._run_once()

    def _run_once(self) -> None:
        try:
            self._scan_factory()
        except Exception:
            logger.exception("skill auto-scan failed")


def run_skill_scan() -> None:
    """Daemon-thread entry point: host an event loop and drive the async scan.

    Mirrors ``docs.scheduler.run_auto_discovery_scan`` — this thread has no loop
    of its own, so ``asyncio.run`` + ``run_in_background_db_scope`` bind a
    per-loop DB engine (no-op on SQLite, required under asyncpg)."""
    import asyncio

    from valuz_agent.infra.db import run_in_background_db_scope

    asyncio.run(run_in_background_db_scope(_arun_skill_scan()))


async def _arun_skill_scan() -> None:
    # Seed the owner ContextVar — this daemon thread's loop does not inherit the
    # main thread's boot-seeded ``valuz_current_user_id`` (see auth_context
    # contract). ``resolve_local_user_id`` is process-cached, so it matches.
    from valuz_agent.infra.auth_context import set_current_user_id
    from valuz_agent.infra.local_identity import resolve_local_user_id

    set_current_user_id(resolve_local_user_id())

    from valuz_agent.api.deps import get_skill_service
    from valuz_agent.infra.eventbus import event_bus
    from valuz_agent.modules.skills.events import SKILL_CHANGED

    gen = get_skill_service()
    svc = await gen.__anext__()
    try:
        indexed = await svc.startup_scan()
        logger.info("skill auto-scan: indexed %d skill(s)", indexed)
        # Refresh any open catalog (the same event the manual endpoint emits).
        event_bus.publish(SKILL_CHANGED, skill_id="*", reason="auto-scan")
    finally:
        try:
            await gen.__anext__()
        except StopAsyncIteration:
            pass


_scheduler: SkillAutoScanScheduler | None = None


def start_skill_auto_scan() -> None:
    global _scheduler
    if _scheduler:
        return
    interval = _interval_sec()
    if interval <= 0:
        logger.info("skill auto-scan disabled (VALUZ_SKILL_SCAN_INTERVAL_SEC <= 0)")
        return
    _scheduler = SkillAutoScanScheduler(run_skill_scan, interval)
    _scheduler.start()


def stop_skill_auto_scan() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.stop()
        _scheduler = None
