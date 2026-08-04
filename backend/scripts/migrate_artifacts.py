"""Backfill delivered artifacts into the versioned tables.

Run AFTER the 0030 migration has created the tables and BEFORE the release that
switches the read/write paths over. The legacy ``valuz_session_artifact`` table
is only ever READ — which is what makes rolling that release back a redeploy
rather than a data restore.

    # 1. Inventory first. Reads nothing but metadata; decides the schedule.
    uv run python scripts/migrate_artifacts.py --dry-run

    # 2. Then migrate, one owner at a time or all of them.
    uv run python scripts/migrate_artifacts.py --commit --owner u_42
    uv run python scripts/migrate_artifacts.py --commit

Both modes write a JSON report to stdout (``--report FILE`` to also save it).
Interrupt and re-run freely: rows already migrated are skipped by their
``legacy_row_id``.

Outcomes
--------
``ok``            migrated, bytes snapshotted
``file_missing``  migrated as a version with no bytes — the deliverable existed,
                  which is worth recording even though the file is gone
``already_done``  skipped, migrated by an earlier run
``not_owned``     NOT migrated. The delivery handler had no owner-boundary check
                  until recently, so rows pointing outside their owner's roots
                  exist; promoting one to a first-class deliverable would make a
                  stale cross-owner reference permanent. If the inventory shows
                  many of these, the likely cause is a legitimate path shape the
                  boundary does not know about (a desktop user's own folder) —
                  widen ``owner_allowed_roots`` rather than migrating them.
``no_project``    NOT migrated. Without a project there is no working directory,
                  so there is nowhere to put the snapshot that the file tree and
                  the resolver would agree on. Reported instead of parked in an
                  invented location.
``not_in_scope``  NOT migrated. Inside the owner's roots but outside the
                  session's project — identity is scope-relative, so there is no
                  key to file it under.
``read_error``    NOT migrated; transient. Re-run.

The last four land in ``flagged`` with their session and path, so they can be
looked at rather than guessed about.
"""

# ruff: noqa: I001 — backend/ must reach sys.path before ``valuz_agent`` imports
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Run directly (``python scripts/migrate_artifacts.py``), so backend/ is not on
# the path yet — same bootstrap as ``scripts/dump_schema.py``.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import valuz_agent.boot.kernel  # noqa: F401,E402 — sys.path side-effect for src.*/app.*


async def _resolve_scope(user_id: str, session_id: str) -> tuple[str, Path] | None:
    """The session's project id and working directory, or None.

    Deliberately NOT the delivery path's resolver: this one ignores worktrees.
    Legacy rows carry no worktree information, and a worktree that existed when
    a file was delivered is long gone by now — so everything migrates into the
    project's own cwd, which is where the design says it belongs.
    """
    from valuz_agent.adapters.data_reader import data_reader
    from valuz_agent.modules.projects.service import project_cwd_by_id

    session = await data_reader().get_session(user_id, session_id)
    if session is None:
        return None
    meta = getattr(session, "metadata", None) or {}
    valuz = meta.get("valuz") or {} if isinstance(meta, dict) else {}
    project_id = str(valuz.get("project_id") or "") if isinstance(valuz, dict) else ""
    if not project_id:
        return None
    cwd = await project_cwd_by_id(user_id, project_id)
    return (project_id, Path(cwd)) if cwd else None


async def _owner_roots(user_id: str) -> list[Path]:
    from valuz_agent.modules.files.service import owner_allowed_roots

    return await owner_allowed_roots(user_id)


async def run(owner: str | None, commit: bool) -> dict[str, object]:
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.modules.artifacts import migration as mig

    async with async_unit_of_work(commit=False) as db:
        rows = await mig._legacy_rows(db, owner)
        plans = await mig.classify(db, rows, resolve_scope=_resolve_scope, owner_roots=_owner_roots)

    report = mig.Report()
    for plan in plans:
        report.record(plan)

    if commit:
        # One transaction per row, not per run: the job is long, and an
        # interruption should leave everything before it durably done rather
        # than rolling back an hour of copying.
        for plan in plans:
            try:
                async with async_unit_of_work() as db:
                    if await mig.apply_plan(db, plan):
                        report.migrated += 1
            except Exception as exc:  # noqa: BLE001 — one bad row must not stop the run
                report.flagged.append(
                    {
                        "row_id": plan.row_id,
                        "user_id": plan.user_id,
                        "session_id": plan.session_id,
                        "file_path": plan.file_path,
                        "outcome": mig.READ_ERROR,
                        "detail": f"{type(exc).__name__}: {exc}",
                    }
                )

    result = report.as_dict()
    result["mode"] = "commit" if commit else "dry-run"
    result["total_size"] = mig.sizeof(report)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--owner", help="Migrate one owner only. Omit for all.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Classify and report; write nothing. The default.",
    )
    mode.add_argument("--commit", action="store_true", help="Write the migrated rows.")
    parser.add_argument("--report", help="Also write the JSON report to this file.")
    args = parser.parse_args(argv)

    result = asyncio.run(run(args.owner, commit=args.commit))
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if args.report:
        Path(args.report).write_text(text + "\n", encoding="utf-8")

    # A run that flagged rows still succeeded — the flags are for a human to
    # read, not a failure. Only an unusable result is an error.
    return 0


if __name__ == "__main__":
    sys.exit(main())
