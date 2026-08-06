"""Removing a task — the one door, for every caller.

A task spans three tables (`valuz_task` + its run index + its timeline) and
none of them has a foreign key, so "delete a task" is a sequence somebody has
to get right. Before this module nobody did: there was no delete of any kind,
and `ProjectService.delete_project` — which carefully removes kernel sessions,
documents, automations, skills and members — walked straight past all three.

The cost of that was not cosmetic. Deleting a project left its tasks `active`
with their kernel sessions gone, so the next boot's `recover_active_tasks`
(a cross-owner `WHERE status='active'` scan with no project-existence check)
respawned a lead against a session id that no longer existed, the loop died on
its first turn, and the health monitor announced a **blocked task for a project
the user had deleted** — a notification about work that cannot be resumed,
attached to a row that cannot be removed.

Ordering is children-first (timeline → runs → header) so an interrupted purge
leaves an orphaned parent rather than orphaned children: `list_active` and the
sidebar read the header, so a surviving header with no children is still
visible and re-purgeable, whereas surviving children are invisible garbage.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.tasks.datastore import (
    TaskDatastore,
    TaskEventDatastore,
    TaskSessionDatastore,
)

logger = logging.getLogger(__name__)


async def purge_tasks(user_id: str, task_ids: Sequence[str]) -> int:
    """Delete these tasks and everything indexed under them. Idempotent.

    Returns the number of task headers removed.

    Does NOT stop a running actor. Every caller either has already torn the
    kernel sessions down (project deletion) or has refused to run on a live
    task (the REST delete rejects `active`). A loop that does outlive its rows
    fails closed: its writes go through `settle_run_if_active` /
    `update_task_status` / `cas_update_plan`, all of which are predicated
    updates that report "no row" rather than resurrecting one.
    """
    if not task_ids:
        return 0
    ids = list(task_ids)
    async with async_unit_of_work() as db:
        events = await TaskEventDatastore(db).delete_for_tasks(user_id, ids)
        runs = await TaskSessionDatastore(db).delete_for_tasks(user_id, ids)
        headers = await TaskDatastore(db).delete_tasks(user_id, ids)
    if headers:
        logger.info(
            "purged %d task(s) for owner %s — %d run row(s), %d event(s)",
            headers,
            user_id,
            runs,
            events,
        )
    return headers


async def purge_project_tasks(user_id: str, project_id: str) -> int:
    """Delete every task belonging to a project. Called from project deletion.

    Separate from `purge_tasks` because the caller knows a project, not a task
    list, and reading that list belongs on this side of the seam.
    """
    async with async_unit_of_work(commit=False) as db:
        task_ids = await TaskDatastore(db).list_ids_by_project(user_id, project_id)
    return await purge_tasks(user_id, task_ids)


__all__ = ["purge_project_tasks", "purge_tasks"]
