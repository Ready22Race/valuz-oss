"""Backfill: ``valuz_session_artifact`` → versioned Artifact / Revision / Content.

Kept out of alembic on purpose. Every legacy row has to be read end to end to be
hashed, which on the cloud deployment is a bucket-wide read; alembic runs at
process start on every replica, so one large project would hold up a release.
This is a separate job you run once the tables exist and before the cutover, and
it only ever READS the legacy table — which is what makes rolling the cutover
back a redeploy rather than a data restore.

Not fail-fast, deliberately. The design said "stop on any row that cannot be
read or verified", but the delivery handler had no owner-boundary check until
recently, so out-of-bounds rows demonstrably exist in the wild, alongside paths
whose files were cleaned up long ago. Stopping on those means the migration
never finishes; instead every row lands in a category and the categories are
reported.

Idempotent at row granularity via ``ArtifactRevisionRow.legacy_row_id``, so the
job can be interrupted and re-run.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.modules.artifacts import snapshot as snap
from valuz_agent.modules.artifacts.datastore import ArtifactDatastore, Scope
from valuz_agent.modules.artifacts.models import REVISION_STATUS_MISSING, ArtifactKind
from valuz_agent.modules.sessions.models import SessionArtifactRow

logger = logging.getLogger(__name__)

# Row outcomes. The first two are migrations; the rest are reported for a human
# to look at, because each means something different about the data.
OK = "ok"
FILE_MISSING = "file_missing"
SUPERSEDED = "superseded"
NOT_OWNED = "not_owned"
NO_PROJECT = "no_project"
NOT_IN_SCOPE = "not_in_scope"
READ_ERROR = "read_error"
ALREADY_DONE = "already_done"

_ACTIONABLE = (OK, FILE_MISSING, SUPERSEDED)

# Outcomes that become a version with no bytes.
_BODILESS = (FILE_MISSING, SUPERSEDED)


@dataclass
class RowPlan:
    """One legacy row, classified — and, when it can be, placed."""

    row_id: str
    user_id: str
    session_id: str
    file_path: str
    file_name: str
    outcome: str
    project_id: str = ""
    scope_cwd: Path | None = None
    rel_path: str = ""
    byte_size: int = 0
    detail: str = ""


@dataclass
class Report:
    """What the run found, and what it did about it."""

    counts: dict[str, int] = field(default_factory=dict)
    total_bytes: int = 0
    migrated: int = 0
    # Rows a human has to decide about, kept with enough context to act on.
    flagged: list[dict[str, str]] = field(default_factory=list)

    def record(self, plan: RowPlan) -> None:
        self.counts[plan.outcome] = self.counts.get(plan.outcome, 0) + 1
        self.total_bytes += plan.byte_size
        if plan.outcome not in _ACTIONABLE and plan.outcome != ALREADY_DONE:
            self.flagged.append(
                {
                    "row_id": plan.row_id,
                    "user_id": plan.user_id,
                    "session_id": plan.session_id,
                    "file_path": plan.file_path,
                    "outcome": plan.outcome,
                    "detail": plan.detail,
                }
            )

    def as_dict(self) -> dict[str, object]:
        return {
            "counts": dict(sorted(self.counts.items())),
            "total_bytes": self.total_bytes,
            "migrated": self.migrated,
            "flagged": self.flagged,
        }


async def _legacy_rows(db: AsyncSession, owner: str | None) -> list[SessionArtifactRow]:
    stmt = select(SessionArtifactRow)
    if owner:
        stmt = stmt.where(SessionArtifactRow.user_id == owner)
    # Ordered so that the same file delivered across several sessions arrives in
    # the order it was produced: the legacy table upserts per (session, path), so
    # three sessions touching one file left three rows, and under scope identity
    # they are one deliverable's v1..v3.
    #
    # Only the LAST of them has bytes, though — see ``SUPERSEDED``. Applying
    # oldest first is what puts the recoverable one at the head and numbers the
    # rest behind it in the order they actually happened.
    stmt = stmt.order_by(
        SessionArtifactRow.user_id,
        SessionArtifactRow.file_path,
        SessionArtifactRow.created_at,
    )
    return list((await db.execute(stmt)).scalars().all())


# ``(user_id, session_id) -> (project_id, scope cwd)``, or None when the session
# has no resolvable project.
ScopeResolver = Callable[[str, str], Awaitable[tuple[str, Path] | None]]
RootsResolver = Callable[[str], Awaitable[list[Path]]]


async def classify(
    db: AsyncSession,
    rows: list[SessionArtifactRow],
    *,
    resolve_scope: ScopeResolver,
    owner_roots: RootsResolver,
) -> list[RowPlan]:
    """Sort every legacy row into an outcome. Reads only.

    ``resolve_scope(user_id, session_id)`` and ``owner_roots(user_id)`` are
    injected so the inventory can run against the real resolvers and the tests
    against fixtures.
    """
    ds = ArtifactDatastore(db)
    plans: list[RowPlan] = []
    roots_cache: dict[str, list[Path]] = {}

    # Which row is the last delivery of each path. The legacy table stored a
    # path, not content, so every row for one path resolves to the SAME bytes on
    # disk today — whatever the most recent delivery left there. Earlier rows
    # are real generations whose content no longer exists.
    last_row_for_path: dict[tuple[str, str], str] = {}
    for row in rows:
        last_row_for_path[(row.user_id, row.file_path)] = row.id

    for row in rows:
        plan = RowPlan(
            row_id=row.id,
            user_id=row.user_id,
            session_id=row.session_id,
            file_path=row.file_path,
            file_name=row.file_name,
            outcome=OK,
        )

        if await ds.find_revision_by_legacy_id(row.user_id, row.id) is not None:
            plan.outcome = ALREADY_DONE
            plans.append(plan)
            continue

        resolved = await resolve_scope(row.user_id, row.session_id)
        if resolved is None:
            # No project means no working directory, so there is nowhere to put
            # the snapshot that the file tree and the resolver would agree on.
            # Reported rather than parked somewhere invented — see the module
            # docstring in ``scripts/migrate_artifacts.py``.
            plan.outcome = NO_PROJECT
            plan.detail = "session has no resolvable project"
            plans.append(plan)
            continue
        plan.project_id, plan.scope_cwd = resolved

        if row.user_id not in roots_cache:
            roots_cache[row.user_id] = await owner_roots(row.user_id)
        roots = roots_cache[row.user_id]

        abs_path = Path(row.file_path)
        from valuz_agent.modules.files.service import assert_owned

        try:
            assert_owned(abs_path, roots)
        except PermissionError:
            # These exist because the delivery handler had no boundary check.
            # Migrating them would turn a stale cross-owner reference into a
            # first-class deliverable, so they are never migrated.
            plan.outcome = NOT_OWNED
            plan.detail = "path is outside the owner's roots"
            plans.append(plan)
            continue

        try:
            plan.rel_path = str(abs_path.relative_to(plan.scope_cwd))
        except ValueError:
            plan.outcome = NOT_IN_SCOPE
            plan.detail = f"path is not under {plan.scope_cwd}"
            plans.append(plan)
            continue

        if last_row_for_path[(row.user_id, row.file_path)] != row.id:
            # An earlier delivery of a path that was delivered again later. It
            # happened, so it is kept as a generation — but its bytes were
            # overwritten in place by the delivery that followed, and the legacy
            # table never held content. Recording it as a version with the
            # CURRENT file's bytes would attribute today's content to a session
            # that produced something else.
            plan.outcome = SUPERSEDED
            plan.detail = "content overwritten by a later delivery of the same path"
            plans.append(plan)
            continue

        try:
            if abs_path.is_file():
                plan.byte_size = abs_path.stat().st_size
            else:
                plan.outcome = FILE_MISSING
                plan.detail = "file no longer exists"
        except OSError as exc:
            plan.outcome = READ_ERROR
            plan.detail = str(exc)
        plans.append(plan)

    return plans


async def apply_plan(db: AsyncSession, plan: RowPlan) -> bool:
    """Migrate one classified row. Returns whether anything was written.

    Rows with no recoverable bytes (``file_missing``, ``superseded``) still
    become revisions, with ``status=missing``. Dropping them would erase the
    fact that those generations happened; keeping them lets the UI say the
    deliverable has history whose content is gone, rather than show nothing.
    """
    if plan.outcome not in _ACTIONABLE or plan.scope_cwd is None:
        return False

    ds = ArtifactDatastore(db)
    scope = Scope(user_id=plan.user_id, project_id=plan.project_id)
    artifact = await ds.find_by_keys(scope, rel_path=plan.rel_path, display_name=plan.file_name)
    if artifact is None:
        artifact = await ds.create_artifact(
            scope,
            # ``file`` for everything. Kind is the caller's statement of what a
            # deliverable IS, and a legacy row has no caller left to ask —
            # inferring one from the extension would put a confident label on a
            # guess. A user can correct it; a wrong label nobody knows is a
            # guess cannot be corrected.
            kind=ArtifactKind.FILE.value,
            display_name=plan.file_name,
            rel_path=plan.rel_path,
        )

    head = await ds.get_head(plan.user_id, artifact.id)
    version_no = (head.version_no + 1) if head else 1

    if plan.outcome in _BODILESS:
        content = await ds.create_content(
            plan.user_id,
            content_hash=f"missing:{plan.row_id}",
            byte_size=0,
            mime_type=snap.guess_mime(plan.file_name),
            storage_key=None,
        )
        stored: str | None = None
        status = REVISION_STATUS_MISSING
    else:
        source = Path(plan.file_path)
        content_hash, byte_size = snap.hash_and_size(source)
        existing = await ds.find_revision_by_content(plan.user_id, artifact.id, content_hash)
        if existing is not None:
            # Identical bytes already recorded for this deliverable: the legacy
            # table held the same file under two sessions. One version, not two.
            return False
        stored = str(
            snap.write_snapshot(source, plan.scope_cwd, artifact.id, version_no, plan.file_name)
        )
        content_row = await ds.find_content_by_hash(plan.user_id, content_hash)
        content = content_row or await ds.create_content(
            plan.user_id,
            content_hash=content_hash,
            byte_size=byte_size,
            mime_type=snap.guess_mime(plan.file_name),
            storage_key=stored,
        )
        status = "ready"

    revision = await ds.append_revision(
        plan.user_id,
        artifact.id,
        expected_head_revision_id=head.revision_id if head else None,
        content=content,
        file_name=plan.file_name,
        abs_path=stored,
        file_format=snap.format_for(plan.file_name),
        source_session_id=plan.session_id,
        status=status,
        legacy_row_id=plan.row_id,
    )
    return revision is not None


def sizeof(report: Report) -> str:
    mb = report.total_bytes / (1024 * 1024)
    return f"{mb:.1f} MiB"


__all__ = [
    "ALREADY_DONE",
    "FILE_MISSING",
    "SUPERSEDED",
    "NOT_IN_SCOPE",
    "NOT_OWNED",
    "NO_PROJECT",
    "OK",
    "READ_ERROR",
    "Report",
    "RowPlan",
    "apply_plan",
    "classify",
    "sizeof",
]
