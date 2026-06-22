"""One-time backfill: per-project connector selection (filesystem → DB).

Pre-DB desktop installs kept the per-project connector selection in
``<project>/.claude/project-config.json`` (the ``connectors`` key). After it
moved to ``valuz_project_connector``, this imports any legacy selection on first
boot so existing users keep their picks.

(The connector *credentials* — header/param values + OAuth tokens — are migrated
straight off the ``FileSecretStore`` files into the unified ``valuz_connector``
columns by migration 0004, not here.)

DB-authoritative and idempotent: a project that already carries a selection is
never overwritten, and a marker file makes the whole pass a one-time event. A
fresh install or shared backend (no local files) is a no-op.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.infra.config import settings
from valuz_agent.modules.connectors.datastore import ConnectorDatastore
from valuz_agent.modules.projects.models import ProjectRow

logger = logging.getLogger(__name__)

_MARKER_NAME = ".connector_fs_backfilled"


async def backfill_connector_fs(db: AsyncSession) -> None:
    """Import the legacy per-project connector selection into the DB exactly once."""
    marker = settings.data_dir / _MARKER_NAME
    if marker.exists():
        return
    await _backfill_project_selection(db)
    try:
        marker.write_text("done\n", encoding="utf-8")
    except OSError:
        # Marker is an optimisation only — the backfill itself is idempotent
        # (DB wins), so failing to write it just re-runs a no-op pass.
        logger.debug("could not write connector backfill marker", exc_info=True)


async def _backfill_project_selection(db: AsyncSession) -> None:
    ds = ConnectorDatastore(db)
    rows = list((await db.execute(select(ProjectRow))).scalars().all())
    migrated = 0
    for proj in rows:
        if proj.kind != "project" or not proj.root_path:
            continue
        # DB wins: never clobber a project that already carries a selection.
        if await ds.get_project_connectors(proj.user_id, proj.id):
            continue
        config_path = Path(proj.root_path) / ".claude" / "project-config.json"
        if not config_path.is_file():
            continue
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        slugs = raw.get("connectors", [])
        if not isinstance(slugs, list) or not slugs:
            continue
        await ds.set_project_connectors(proj.user_id, proj.id, [str(s) for s in slugs])
        migrated += 1
    if migrated:
        logger.info("connector selection backfill: imported %d project(s) into DB", migrated)
