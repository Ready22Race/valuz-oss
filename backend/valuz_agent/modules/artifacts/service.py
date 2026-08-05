"""Recording a deliverable. The one place a delivery is carried out.

Callers
-------
The ``deliver_artifacts`` MCP tool is the first, not the only one. Any module
that produces something the user should keep — a generated UI, a rendered
report, a scheduled export — records it here, so all of them get the same
identity matching, the same content-hash idempotency, the same head
compare-and-set and the same owner boundary. A second implementation of any of
those would be a second set of rules for what a deliverable is.

Boundaries
----------
This layer decides; it does not translate. Outcomes come back as a
``DeliveryStatus`` and the caller words them for its own audience — the MCP tool
turns them into prose a model reads, an HTTP route would turn them into a status
code. Wire formats stay out.

Ownership is never ambient: the caller passes the scope it resolved, and every
path is checked against the owner's roots before anything reads it.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.modules.artifacts import snapshot as snap
from valuz_agent.modules.artifacts.datastore import ArtifactDatastore, Scope
from valuz_agent.modules.artifacts.models import REVISION_STATUS_READY, ArtifactKind

logger = logging.getLogger(__name__)


class DeliveryStatus(StrEnum):
    """How one delivery ended. Every branch below returns one of these."""

    RECORDED = "recorded"
    #: Same bytes already recorded for this deliverable — a replay, a transport
    #: retry, or a caller re-delivering something it never changed.
    UNCHANGED = "unchanged"
    #: Outside the owner's roots. Checked before the file is touched, so it is
    #: also the answer for a path that does not exist out there.
    NOT_OWNED = "not_owned"
    #: Owned, but not under this delivery's working directory. Identity is
    #: scope-relative, so there is no key to file it under.
    NOT_IN_SCOPE = "not_in_scope"
    NOT_FOUND = "not_found"
    #: Inside ``.artifact/``. Delivering a snapshot would record a version whose
    #: content is the previous version, without end.
    IN_ARTIFACT_STORE = "in_artifact_store"
    #: Another delivery moved the head first. The caller re-reads and retries.
    STALE_HEAD = "stale_head"
    SNAPSHOT_FAILED = "snapshot_failed"
    #: ``artifact_id`` names nothing in this scope.
    UNKNOWN_ARTIFACT = "unknown_artifact"
    INVALID = "invalid"


@dataclass(frozen=True)
class DeliveryRequest:
    """What a caller knows and the server cannot work out for itself.

    Size, MIME and content hash are deliberately absent: they are properties of
    the bytes, which this layer is about to read.
    """

    #: The file to record. A future request form may carry content instead of a
    #: path; that is a change to this shape, not a second entry point.
    abs_path: Path
    #: Label for the deliverable. Defaults to the file's basename.
    display_name: str | None = None
    #: What the deliverable IS. Never inferred — an extension says how a file is
    #: encoded, not what it is for.
    kind: ArtifactKind = ArtifactKind.FILE
    mime_type: str | None = None
    #: Continue this deliverable although the file no longer looks like it — a
    #: rename or a move, which key matching cannot recognise.
    artifact_id: str | None = None
    #: The opposite: start a separate deliverable although the name matches one
    #: already recorded.
    as_new_artifact: bool = False


@dataclass(frozen=True)
class DeliveryResult:
    status: DeliveryStatus
    artifact_id: str | None = None
    revision_id: str | None = None
    version_no: int | None = None
    is_new_version: bool = False
    #: Absolute path of the recorded snapshot — what a caller hands on for
    #: reading or linking. ``None`` unless the delivery was recorded.
    abs_path: str | None = None
    #: Only for ``INVALID``, where the caller needs to know which input was.
    detail: str | None = None

    @property
    def ok(self) -> bool:
        return self.status in (DeliveryStatus.RECORDED, DeliveryStatus.UNCHANGED)


async def deliver_artifact(
    db: AsyncSession,
    *,
    scope: Scope,
    scope_cwd: Path,
    owner_roots: list[Path],
    request: DeliveryRequest,
    source_session_id: str | None = None,
) -> DeliveryResult:
    """Record a version of a deliverable, creating the deliverable if it is new.

    Named for the act, not for what is handed in. Today a request carries a
    path, and a second input form (content with no file, which GenUI would need)
    belongs in ``DeliveryRequest`` rather than in a second function — the
    outcome is the same either way, and splitting on input shape would make two
    names for one thing.

    Never raises for bad input: every rejection is a ``DeliveryStatus``, so a
    caller handling several files reports each one instead of losing the batch
    to the first bad entry. Commits nothing — the caller's unit of work decides,
    which is what lets a batch be all-or-nothing.
    """
    ds = ArtifactDatastore(db)
    abs_path = Path(os.path.abspath(os.path.expanduser(str(request.abs_path))))

    if request.artifact_id and request.as_new_artifact:
        return DeliveryResult(
            status=DeliveryStatus.INVALID,
            detail="artifact_id and as_new_artifact say opposite things",
        )

    # Owner boundary first, before anything reads the path — otherwise the
    # difference between "not found" and "not yours" would tell a caller whether
    # somebody else holds it.
    from valuz_agent.modules.files.service import assert_owned

    try:
        assert_owned(abs_path, owner_roots)
    except PermissionError:
        logger.warning("deliver: refused out-of-bounds path for owner %s", scope.user_id)
        return DeliveryResult(status=DeliveryStatus.NOT_OWNED)

    if snap.is_inside_artifact_root(abs_path, scope_cwd):
        return DeliveryResult(status=DeliveryStatus.IN_ARTIFACT_STORE)

    try:
        rel_path = str(abs_path.relative_to(scope_cwd))
    except ValueError:
        return DeliveryResult(status=DeliveryStatus.NOT_IN_SCOPE)

    if not abs_path.is_file():
        return DeliveryResult(status=DeliveryStatus.NOT_FOUND)

    display_name = request.display_name or abs_path.name
    mime_type = request.mime_type or snap.guess_mime(display_name)

    artifact = None
    if request.artifact_id:
        # Scoped lookup: an id is a bare string a caller could have carried over
        # from another project or worktree, and resolving it unscoped would let
        # a delivery append a version to somebody else's deliverable.
        artifact = await ds.get_artifact_in_scope(scope, request.artifact_id)
        if artifact is None:
            return DeliveryResult(status=DeliveryStatus.UNKNOWN_ARTIFACT)
    elif not request.as_new_artifact:
        artifact = await ds.find_by_keys(scope, rel_path=rel_path, display_name=display_name)

    if artifact is None:
        artifact = await ds.create_artifact(
            scope, kind=request.kind.value, display_name=display_name, rel_path=rel_path
        )
    else:
        # Follow the file: a deliverable that moved or was renamed has to be
        # findable at its new path next time, not only at the one it left.
        await ds.adopt_delivery(scope, artifact, rel_path=rel_path, display_name=display_name)

    # The head can move under us: a runtime may emit several tool_use blocks in
    # one turn, and two deliveries to the same deliverable then race. Losing
    # that race is ordinary, not an error the caller should have to handle — so
    # re-read and try again once before reporting it. Only a second loss, which
    # means sustained contention, becomes STALE_HEAD.
    for attempt in range(2):
        # Both, together: the head gives the version to build on and the value
        # the compare-and-set expects, its revision carries the hash that says
        # whether this delivery changed anything. Re-read each round — the
        # delivery that beat us may have recorded these very bytes.
        current = await ds.get_head_with_revision(scope.user_id, artifact.id)
        head, head_revision = current if current is not None else (None, None)
        version_no = (head.version_no + 1) if head is not None else 1

        try:
            # Copied inside the loop because the destination carries the version
            # number, so a retry needs its own. The bytes are hashed on the way
            # through rather than in a separate pass: both need to read the whole
            # file, and on the cloud deployment that read crosses an object mount.
            staged = await asyncio.to_thread(
                snap.stage_snapshot,
                abs_path,
                scope_cwd,
                artifact.id,
                version_no,
                display_name,
            )
        except OSError:
            logger.warning("deliver: snapshot failed for %s", abs_path, exc_info=True)
            return DeliveryResult(status=DeliveryStatus.SNAPSHOT_FAILED)

        # A replay, a transport retry, or a caller re-delivering something it
        # never changed. Compared against the head alone: a revision further
        # back holding these bytes means the caller is RETURNING to it, which is
        # a new generation and not a no-op. ``ready`` as well, because a head
        # whose file is gone must not absorb the delivery that would restore it.
        if (
            head_revision is not None
            and head_revision.content_hash == staged.content_hash
            and head_revision.status == REVISION_STATUS_READY
        ):
            await asyncio.to_thread(snap.discard_snapshot, staged)
            return DeliveryResult(
                status=DeliveryStatus.UNCHANGED,
                artifact_id=artifact.id,
                revision_id=head_revision.id,
                version_no=head_revision.version_no,
                abs_path=head_revision.abs_path,
            )

        content = await ds.find_content_by_hash(scope.user_id, staged.content_hash)
        if content is None:
            content = await ds.create_content(
                scope.user_id,
                content_hash=staged.content_hash,
                byte_size=staged.byte_size,
                storage_key=str(staged.final),
            )

        revision = await ds.append_revision(
            scope.user_id,
            artifact.id,
            expected_head_revision_id=head.revision_id if head is not None else None,
            content=content,
            file_name=display_name,
            abs_path=str(staged.final),
            file_format=snap.format_for(display_name),
            mime_type=mime_type,
            source_session_id=source_session_id,
        )
        if revision is not None:
            break
        # The winner's snapshot is at the path this attempt staged for, so the
        # staged copy is dropped rather than promoted — publishing it would put
        # these bytes under the row that recorded the winner's.
        await asyncio.to_thread(snap.discard_snapshot, staged)
        logger.info("deliver: head moved under %s, retrying (attempt %d)", artifact.id, attempt + 1)
    else:
        return DeliveryResult(status=DeliveryStatus.STALE_HEAD)

    try:
        # Last, and only for the delivery that won the head — see
        # ``stage_snapshot``.
        stored = await asyncio.to_thread(snap.promote_snapshot, staged)
    except OSError:
        logger.warning("deliver: could not publish snapshot for %s", abs_path, exc_info=True)
        await asyncio.to_thread(snap.discard_snapshot, staged)
        # The generation is on record but its bytes never landed. Marking it
        # keeps the history honest — the read paths already gate on status — and
        # it is also what lets a retry through: the replay check above requires
        # a head whose bytes are actually there, so the next attempt sees a
        # change rather than a no-op.
        await ds.mark_revision_missing(scope.user_id, revision.id)
        return DeliveryResult(
            status=DeliveryStatus.SNAPSHOT_FAILED,
            artifact_id=artifact.id,
            revision_id=revision.id,
        )

    return DeliveryResult(
        status=DeliveryStatus.RECORDED,
        artifact_id=artifact.id,
        revision_id=revision.id,
        version_no=revision.version_no,
        is_new_version=revision.version_no > 1,
        abs_path=str(stored),
    )


__all__ = [
    "DeliveryRequest",
    "DeliveryResult",
    "DeliveryStatus",
    "deliver_artifact",
]
