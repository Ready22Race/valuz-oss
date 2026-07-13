from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from watchfiles import awatch

logger = logging.getLogger(__name__)


class SkillFileWatcher:
    """Watch the local skill roots and re-index on out-of-band edits.

    A user can edit a ``SKILL.md`` directly on disk (external editor, dropped
    folder) without going through the mutation API. ``valuz_skill_index`` — the
    catalog read path — is only refreshed by the boot scan, every mutation, and
    the periodic auto-scan, so a raw disk edit would otherwise stay invisible
    until the next (default 5-min) auto-scan tick. This watcher closes that gap:
    a debounced filesystem change triggers a full ``reindex`` pass, dropping the
    reflect-an-external-edit latency from minutes to ~300 ms. The reindex is the
    single source of truth for freshness; clients pick the fresh index up on
    their next fetch (navigate / window-focus revalidation).
    """

    def __init__(self, reindex: Callable[[], Awaitable[None]]) -> None:
        self._reindex = reindex
        self._paths: set[Path] = set()
        self._task: asyncio.Task | None = None  # type: ignore[type-arg]

    def add_path(self, path: Path) -> None:
        self._paths.add(path)

    def remove_path(self, path: Path) -> None:
        self._paths.discard(path)

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._watch_loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _watch_loop(self) -> None:
        while True:
            active_paths = [p for p in self._paths if p.exists()]
            if not active_paths:
                await asyncio.sleep(5)
                continue
            try:
                # ``awatch``'s debounce coalesces a burst of saves into one
                # batch; any batch under a watched root triggers a single
                # reindex pass (``startup_scan`` is idempotent and cheap).
                async for _changes in awatch(*active_paths, debounce=300):
                    try:
                        await self._reindex()
                    except Exception:
                        logger.exception("skill reindex after file change failed")
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("File watcher error, restarting in 5s")
                await asyncio.sleep(5)
