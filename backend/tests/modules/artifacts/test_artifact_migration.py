"""Backfilling the legacy artifact table into the versioned one.

The two things worth getting right are what the migration *refuses* to do and
what it does with rows that are not clean. Every legacy row lands in a category;
only two of them migrate, and both of those have to be re-runnable.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.infra.database import Base
from valuz_agent.modules.artifacts import migration as mig
from valuz_agent.modules.artifacts.datastore import ArtifactDatastore, Scope
from valuz_agent.modules.artifacts.models import (
    REVISION_STATUS_MISSING,
    REVISION_STATUS_READY,
    ArtifactContentRow,
    ArtifactHeadRow,
    ArtifactKeyRow,
    ArtifactRevisionRow,
    ArtifactRow,
)
from valuz_agent.modules.sessions.models import SessionArtifactRow

_TABLES = [
    ArtifactRow.__table__,
    ArtifactKeyRow.__table__,
    ArtifactHeadRow.__table__,
    ArtifactRevisionRow.__table__,
    ArtifactContentRow.__table__,
    SessionArtifactRow.__table__,
]


@pytest.fixture
def session_factory(tmp_path):  # type: ignore[no-untyped-def]
    db_file = tmp_path / "migrate.db"
    sync_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(sync_engine, tables=_TABLES)
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    return async_sessionmaker(bind=async_engine, expire_on_commit=False)


@pytest.fixture
def cwd(tmp_path):  # type: ignore[no-untyped-def]
    """The project working directory. ``tmp_path`` stays outside it."""
    workdir = tmp_path / "project"
    workdir.mkdir()
    return workdir


@pytest.fixture
def resolvers(cwd, tmp_path):  # type: ignore[no-untyped-def]
    """Scope + owner-root resolvers, pinned. ``s-noproject`` has none."""

    async def resolve_scope(user_id: str, session_id: str):  # type: ignore[no-untyped-def]
        if session_id == "s-noproject":
            return None
        return ("p1", cwd)

    async def owner_roots(user_id: str) -> list[Path]:
        # Wider than the project cwd on purpose, so "owned but out of scope" is
        # reachable and distinguishable from "not owned at all".
        return [(tmp_path / "owned").resolve()]

    return {"resolve_scope": resolve_scope, "owner_roots": owner_roots}


async def _legacy(session_factory, *, row_id: str, path: Path, session_id: str = "s1") -> None:  # type: ignore[no-untyped-def]
    async with session_factory() as db:
        db.add(
            SessionArtifactRow(
                id=row_id,
                user_id="u1",
                session_id=session_id,
                file_path=str(path),
                file_name=path.name,
                file_size=path.stat().st_size if path.exists() else 0,
                mime_type="text/markdown",
            )
        )
        await db.commit()


async def _classify(session_factory, resolvers) -> list[mig.RowPlan]:  # type: ignore[no-untyped-def]
    async with session_factory() as db:
        rows = await mig._legacy_rows(db, None)
        return await mig.classify(db, rows, **resolvers)


async def _migrate(session_factory, resolvers) -> mig.Report:  # type: ignore[no-untyped-def]
    plans = await _classify(session_factory, resolvers)
    report = mig.Report()
    for plan in plans:
        report.record(plan)
        async with session_factory() as db:
            if await mig.apply_plan(db, plan):
                report.migrated += 1
            await db.commit()
    return report


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ── Classification ────────────────────────────────────────────────────────────


async def test_a_readable_owned_row_migrates(session_factory, resolvers, cwd, tmp_path):  # type: ignore[no-untyped-def]
    owned = tmp_path / "owned"
    owned.mkdir()
    # The project cwd has to sit inside the owner root for the row to be owned.
    src = _write(owned / "report.md", "v1")
    await _legacy(session_factory, row_id="L1", path=src)

    async def resolve_scope(user_id: str, session_id: str):  # type: ignore[no-untyped-def]
        return ("p1", owned)

    plans = await _classify(session_factory, {**resolvers, "resolve_scope": resolve_scope})

    assert [p.outcome for p in plans] == [mig.OK]
    assert plans[0].rel_path == "report.md"
    assert plans[0].byte_size == 2


async def test_a_row_outside_the_owner_roots_is_refused(session_factory, resolvers, tmp_path):  # type: ignore[no-untyped-def]
    """These exist because the handler had no boundary check.

    Migrating one would turn a stale cross-owner reference into a first-class
    deliverable, which is worse than leaving it in a table about to be dropped.
    """
    intruder = _write(tmp_path / "elsewhere" / "theirs.md", "not yours")
    await _legacy(session_factory, row_id="L1", path=intruder)

    plans = await _classify(session_factory, resolvers)

    assert [p.outcome for p in plans] == [mig.NOT_OWNED]


async def test_a_row_without_a_project_is_reported_not_invented(
    session_factory, resolvers, tmp_path
):  # type: ignore[no-untyped-def]
    """No project → no working directory → nowhere the snapshot could live that
    the file tree and the resolver would both agree on."""
    owned = tmp_path / "owned"
    src = _write(owned / "report.md", "v1")
    await _legacy(session_factory, row_id="L1", path=src, session_id="s-noproject")

    plans = await _classify(session_factory, resolvers)

    assert [p.outcome for p in plans] == [mig.NO_PROJECT]


async def test_an_owned_row_outside_the_project_is_refused(session_factory, resolvers, tmp_path):  # type: ignore[no-untyped-def]
    """Owned, but identity is scope-relative — there is no key to file it under."""
    owned = tmp_path / "owned"
    src = _write(owned / "sibling" / "report.md", "v1")
    await _legacy(session_factory, row_id="L1", path=src)

    plans = await _classify(session_factory, resolvers)

    assert [p.outcome for p in plans] == [mig.NOT_IN_SCOPE]


async def test_a_row_whose_file_is_gone_still_migrates(session_factory, resolvers, tmp_path):  # type: ignore[no-untyped-def]
    """Dropping it would erase the fact that the deliverable ever existed."""
    owned = tmp_path / "owned"
    owned.mkdir()
    ghost = owned / "gone.md"
    await _legacy(session_factory, row_id="L1", path=ghost)

    async def resolve_scope(user_id: str, session_id: str):  # type: ignore[no-untyped-def]
        return ("p1", owned)

    report = await _migrate(session_factory, {**resolvers, "resolve_scope": resolve_scope})

    assert report.counts == {mig.FILE_MISSING: 1}
    assert report.migrated == 1
    async with session_factory() as db:
        ds = ArtifactDatastore(db)
        (artifact, _head, revision) = (await ds.list_scope_heads(Scope("u1", "p1")))[0]
        assert artifact.display_name == "gone.md"
        assert revision.status == REVISION_STATUS_MISSING
        assert revision.abs_path is None


# ── Applying ──────────────────────────────────────────────────────────────────


@pytest.fixture
def owned_project(tmp_path, resolvers):  # type: ignore[no-untyped-def]
    """Owner root and project cwd as the same directory — the common case."""
    owned = tmp_path / "owned"
    owned.mkdir()

    async def resolve_scope(user_id: str, session_id: str):  # type: ignore[no-untyped-def]
        return ("p1", owned)

    return owned, {**resolvers, "resolve_scope": resolve_scope}


async def test_earlier_deliveries_of_a_path_keep_provenance_but_not_bytes(
    session_factory, owned_project
):  # type: ignore[no-untyped-def]
    """Only the LAST delivery of a path has recoverable content.

    The legacy table stored a path, not content, so every row for one path
    resolves to whatever is on disk today. Recording the earlier rows with those
    bytes would attribute the current content to a session that produced
    something else; dropping them would erase that the generations happened. So
    they become versions with ``status=missing`` and the last one carries the
    bytes.
    """
    owned, resolvers = owned_project
    src = _write(owned / "report.md", "v1")
    await _legacy(session_factory, row_id="L1", path=src, session_id="s1")
    src.write_text("v2 — this is what is actually on disk", encoding="utf-8")
    await _legacy(session_factory, row_id="L2", path=src, session_id="s2")

    report = await _migrate(session_factory, resolvers)

    assert report.migrated == 2
    assert report.counts == {mig.SUPERSEDED: 1, mig.OK: 1}
    async with session_factory() as db:
        ds = ArtifactDatastore(db)
        heads = await ds.list_scope_heads(Scope("u1", "p1"))
        assert len(heads) == 1
        artifact, head, head_revision = heads[0]
        # Chronological, and the head is the one whose bytes survived.
        assert head.version_no == 2
        assert head_revision.status == REVISION_STATUS_READY
        revisions = await ds.list_revisions("u1", artifact.id)
        assert [r.source_session_id for r in revisions] == ["s1", "s2"]
        assert [r.status for r in revisions] == [
            REVISION_STATUS_MISSING,
            REVISION_STATUS_READY,
        ]
        assert revisions[0].abs_path is None


async def test_snapshots_are_copies_that_survive_the_original(session_factory, owned_project):  # type: ignore[no-untyped-def]
    owned, resolvers = owned_project
    src = _write(owned / "report.md", "original")
    await _legacy(session_factory, row_id="L1", path=src)

    await _migrate(session_factory, resolvers)

    async with session_factory() as db:
        (_a, _h, revision) = (await ArtifactDatastore(db).list_scope_heads(Scope("u1", "p1")))[0]
    assert revision.abs_path is not None
    stored = Path(revision.abs_path)
    assert ".artifact" in stored.parts
    src.unlink()
    assert stored.read_text(encoding="utf-8") == "original"


async def test_rerunning_migrates_nothing_twice(session_factory, owned_project):  # type: ignore[no-untyped-def]
    """The job is long enough to be interrupted; resuming must be free."""
    owned, resolvers = owned_project
    await _legacy(session_factory, row_id="L1", path=_write(owned / "report.md", "v1"))

    first = await _migrate(session_factory, resolvers)
    second = await _migrate(session_factory, resolvers)

    assert first.migrated == 1
    assert second.migrated == 0
    assert second.counts == {mig.ALREADY_DONE: 1}
    async with session_factory() as db:
        heads = await ArtifactDatastore(db).list_scope_heads(Scope("u1", "p1"))
    assert len(heads) == 1


async def test_only_one_version_ever_carries_bytes_per_path(session_factory, owned_project):  # type: ignore[no-untyped-def]
    """However many legacy rows a path had, exactly one has content to migrate."""
    owned, resolvers = owned_project
    src = _write(owned / "report.md", "unchanged")
    for i, session in enumerate(("s1", "s2", "s3"), start=1):
        await _legacy(session_factory, row_id=f"L{i}", path=src, session_id=session)

    await _migrate(session_factory, resolvers)

    async with session_factory() as db:
        ds = ArtifactDatastore(db)
        (artifact, head, _r) = (await ds.list_scope_heads(Scope("u1", "p1")))[0]
        assert head.version_no == 3
        revisions = await ds.list_revisions("u1", artifact.id)
        assert sum(1 for r in revisions if r.status == REVISION_STATUS_READY) == 1
        assert sum(1 for r in revisions if r.abs_path) == 1


async def test_refused_rows_are_flagged_with_enough_context_to_act(
    session_factory, resolvers, tmp_path
):  # type: ignore[no-untyped-def]
    """A count alone would not say which owner or which path to look at."""
    await _legacy(session_factory, row_id="L1", path=_write(tmp_path / "elsewhere" / "x.md", "x"))

    report = await _migrate(session_factory, resolvers)

    assert report.migrated == 0
    (flag,) = report.flagged
    assert flag["outcome"] == mig.NOT_OWNED
    assert flag["row_id"] == "L1"
    assert flag["session_id"] == "s1"
    assert "x.md" in flag["file_path"]


async def test_report_totals_only_count_migratable_bytes(session_factory, owned_project):  # type: ignore[no-untyped-def]
    """The byte total is there to size the run; refused rows are not copied."""
    owned, resolvers = owned_project
    await _legacy(session_factory, row_id="L1", path=_write(owned / "a.md", "12345"))

    report = await _migrate(session_factory, resolvers)

    assert report.total_bytes == 5
    assert "MiB" in mig.sizeof(report)


async def test_migrated_rows_read_back_as_ready(session_factory, owned_project):  # type: ignore[no-untyped-def]
    owned, resolvers = owned_project
    await _legacy(session_factory, row_id="L1", path=_write(owned / "report.md", "v1"))

    await _migrate(session_factory, resolvers)

    async with session_factory() as db:
        (_a, _h, revision) = (await ArtifactDatastore(db).list_scope_heads(Scope("u1", "p1")))[0]
    assert revision.status == REVISION_STATUS_READY
    assert revision.legacy_row_id == "L1"
    assert revision.content_hash.startswith("sha256:")
