"""``deliver_artifacts`` in-process MCP tool.

A single built-in tool the agent calls to declare finished outputs ("生成文件").
Registered in the host toolkit MCP ``base`` toolset (see ``boot/steps.py``), so
it is loaded into **every** session and is runtime-agnostic
(claude / codex / deepagents), surfacing to models as
``mcp__harness__deliver_artifacts``.

It is the inverse of the upload pipeline: uploads are files the *user* hands the
agent (``valuz_session_attachment``, staged per turn); delivered artifacts are
files the *agent* produced and marks as deliverables. The session panel renders
them as a curated, read-only list the user can open.

What a delivery does
--------------------
Each entry is snapshotted into ``<scope_cwd>/.artifact/`` and recorded as a new
generation of an *artifact* — a stable identity that survives renames and
carries across sessions (see ``modules/artifacts``). Re-delivering the same file
appends a version rather than overwriting the previous one, and past versions
stay readable at their own paths even after the working copy is edited away.

Three properties this handler is responsible for, none of which are obvious from
the outside:

**Owner boundary.** ``filePath`` is model-supplied, so it is checked against the
caller's own roots (``owner_allowed_roots`` + ``assert_owned``, the same
isolation line ``/v1/files/resolve`` uses, symlink-escape guard included) before
anything reads it. The check runs BEFORE the ``isfile`` probe, so an
out-of-bounds path cannot be used as an existence oracle for another tenant's
files. This matters more here than it did when deliveries were mere references:
the handler now *copies bytes*, host-side, from a process that can see the whole
shared mount.

**Idempotency by content.** The MCP layer hands handlers ``(name, arguments)``
and drops ``_meta``, so the runtime's tool_use id is not available to key on —
and a replay carries arguments identical to a genuine second delivery, so it
could not be recovered heuristically either. Re-delivering unchanged bytes
therefore returns the existing revision instead of minting a version, which also
absorbs a transport retry after a lost response.

**One transaction for the batch.** A partial failure must not leave some entries
recorded and others not; the whole batch commits or none of it does. Snapshot
files written before a failure are harmless orphans under ``.artifact`` (no row
references them).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

from src.core import ToolDef, ToolResult
from src.core.tools import ExecContext

import valuz_agent.boot.kernel  # noqa: F401  (sets kernel import path)
from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.artifacts import snapshot as snap
from valuz_agent.modules.artifacts.datastore import ArtifactDatastore
from valuz_agent.modules.artifacts.scope import (
    DeliveryScope,
    ScopeUnavailableError,
    resolve_delivery_scope,
)
from valuz_agent.modules.files.service import assert_owned, owner_allowed_roots

logger = logging.getLogger(__name__)

DELIVER_ARTIFACTS_TOOL_NAME = "deliver_artifacts"

# Per-entry outcomes. The toolkit MCP renders a failed tool result as a text
# prefix rather than a wire error, so the model reads these as prose — each one
# has to say what to do next, not merely that something was refused.
STATUS_RECORDED = "recorded"
STATUS_UNCHANGED = "unchanged"
STATUS_NOT_OWNED = "not_owned"
STATUS_NOT_IN_SCOPE = "not_in_scope"
STATUS_NOT_FOUND = "not_found"
STATUS_IN_ARTIFACT_STORE = "in_artifact_store"
STATUS_STALE_HEAD = "stale_head"
STATUS_SNAPSHOT_FAILED = "snapshot_failed"
STATUS_INVALID = "invalid"

_ERRORS = {
    STATUS_NOT_OWNED: (
        "path is outside your workspace — write the file into your working "
        "directory and deliver it from there"
    ),
    STATUS_NOT_IN_SCOPE: (
        "path is outside this session's working directory — write the file "
        "there and deliver it from there"
    ),
    STATUS_NOT_FOUND: "file not found — check the path you wrote",
    STATUS_IN_ARTIFACT_STORE: (
        "that path is inside the artifact store, which holds already-delivered "
        "versions — deliver the file from your working directory instead"
    ),
    STATUS_STALE_HEAD: (
        "someone recorded a newer version of this deliverable while you were "
        "working — read the current version and apply your change to it"
    ),
    STATUS_SNAPSHOT_FAILED: "could not copy the file — you can retry this delivery",
}

TOOL_DESCRIPTION = (
    "Register finished output files as deliverables — they show up in the "
    "user's '生成文件' (Generated Files) panel, which the user can open. Pass an "
    "'attachments' array; each entry needs a 'filePath' (absolute path to a "
    "file you already wrote, inside your working directory). 'fileName', "
    "'fileSize' and 'mimeType' are optional and derived from the file on disk. "
    "Delivering a file you have delivered before records a NEW VERSION of the "
    "same deliverable rather than replacing it — earlier versions stay readable "
    "at the 'absPath' each delivery returns. Keep the same file name when you "
    "revise something, so it is recognised as the same deliverable; use a "
    "different name (or set 'asNewArtifact': true) only when the user asked for "
    "a genuinely new deliverable. Delivering unchanged content is a no-op. When "
    "you mention a delivered file in your reply text, link it by joining "
    "`valuz-file://` with the returned absolute 'absPath' (which starts with "
    "`/`), giving three slashes — e.g. "
    "[report.md](valuz-file:///Users/you/proj/.artifact/A7K2PH3M/v2/report.md) — "
    "so the client can open it (it resolves to a local path or a signed URL "
    "depending on where the file lives). Never write into the .artifact "
    "directory yourself."
)

_PARAMS = {
    "type": "object",
    "properties": {
        "attachments": {
            "type": "array",
            "description": "The deliverable files to register.",
            "items": {
                "type": "object",
                "properties": {
                    "filePath": {
                        "type": "string",
                        "description": (
                            "Absolute path to a file you already wrote, inside "
                            "your working directory."
                        ),
                    },
                    "fileName": {
                        "type": "string",
                        "description": "Display name. Defaults to the file's basename.",
                    },
                    "fileSize": {
                        "type": "integer",
                        "description": "Size in bytes. Derived from disk when omitted.",
                    },
                    "mimeType": {
                        "type": "string",
                        "description": "MIME type. Guessed from the extension when omitted.",
                    },
                    "asNewArtifact": {
                        "type": "boolean",
                        "description": (
                            "Force a separate deliverable even if the name "
                            "matches one you delivered before. Use only when "
                            "the user asked for a new deliverable rather than "
                            "a revision."
                        ),
                    },
                },
                "required": ["filePath"],
            },
            "minItems": 1,
        }
    },
    "required": ["attachments"],
}


def _fail(file_path: Any, status: str, detail: str | None = None) -> dict[str, Any]:
    return {
        "filePath": str(file_path),
        "status": status,
        "error": detail or _ERRORS.get(status, status),
    }


async def _deliver_one(
    ds: ArtifactDatastore,
    delivery: DeliveryScope,
    raw: dict[str, Any],
    *,
    roots: list[Path],
    session_id: str,
    user_id: str,
) -> dict[str, Any]:
    """Record one entry. Returns the per-item result — never raises for input."""
    file_path = raw.get("filePath")
    if not file_path or not isinstance(file_path, str):
        return _fail(file_path, STATUS_INVALID, "missing 'filePath'")

    abs_path = Path(os.path.abspath(os.path.expanduser(file_path)))

    # Owner boundary first — see the module docstring.
    try:
        assert_owned(abs_path, roots)
    except PermissionError:
        logger.warning("deliver_artifacts: refused out-of-bounds path for owner %s", user_id)
        return _fail(file_path, STATUS_NOT_OWNED)

    if snap.is_inside_artifact_root(abs_path, delivery.cwd):
        return _fail(file_path, STATUS_IN_ARTIFACT_STORE)

    try:
        rel_path = str(abs_path.relative_to(delivery.cwd))
    except ValueError:
        # Owned, but belonging to a different project or worktree. Identity is
        # scope-relative, so there is no key to file this under.
        return _fail(file_path, STATUS_NOT_IN_SCOPE)

    if not abs_path.is_file():
        return _fail(file_path, STATUS_NOT_FOUND)

    file_name = str(raw.get("fileName") or abs_path.name)
    mime_type = raw.get("mimeType") or snap.guess_mime(file_name)

    # Server-side and authoritative: hash and size come from the bytes, not from
    # what the model claimed about them.
    try:
        content_hash, byte_size = await asyncio.to_thread(snap.hash_and_size, abs_path)
    except OSError:
        logger.warning("deliver_artifacts: could not read %s", abs_path, exc_info=True)
        return _fail(file_path, STATUS_SNAPSHOT_FAILED)

    scope = delivery.scope
    artifact = None
    if not raw.get("asNewArtifact"):
        artifact = await ds.find_by_keys(scope, rel_path=rel_path, display_name=file_name)
    if artifact is None:
        artifact = await ds.create_artifact(
            scope,
            kind=snap.kind_for(file_name, mime_type),
            display_name=file_name,
            rel_path=rel_path,
        )

    existing = await ds.find_revision_by_content(scope.user_id, artifact.id, content_hash)
    if existing is not None:
        # Same bytes, same deliverable: a replay, a retry, or the agent
        # delivering something it never changed.
        return {
            "filePath": file_path,
            "status": STATUS_UNCHANGED,
            "artifactId": artifact.id,
            "revisionId": existing.id,
            "versionNo": existing.version_no,
            "isNewVersion": False,
            "absPath": existing.abs_path,
        }

    head = await ds.get_head(scope.user_id, artifact.id)
    version_no = (head.version_no + 1) if head is not None else 1

    try:
        stored = await asyncio.to_thread(
            snap.write_snapshot, abs_path, delivery.cwd, artifact.id, version_no, file_name
        )
    except OSError:
        logger.warning("deliver_artifacts: snapshot failed for %s", abs_path, exc_info=True)
        return _fail(file_path, STATUS_SNAPSHOT_FAILED)

    content = await ds.find_content_by_hash(scope.user_id, content_hash)
    if content is None:
        content = await ds.create_content(
            scope.user_id,
            content_hash=content_hash,
            byte_size=byte_size,
            mime_type=mime_type,
            storage_key=str(stored),
        )

    revision = await ds.append_revision(
        scope.user_id,
        artifact.id,
        expected_head_revision_id=head.revision_id if head is not None else None,
        content=content,
        file_name=file_name,
        abs_path=str(stored),
        file_format=snap.format_for(file_name),
        source_session_id=session_id,
    )
    if revision is None:
        return _fail(file_path, STATUS_STALE_HEAD)

    return {
        "filePath": file_path,
        "status": STATUS_RECORDED,
        "artifactId": artifact.id,
        "revisionId": revision.id,
        "versionNo": revision.version_no,
        "isNewVersion": revision.version_no > 1,
        "absPath": str(stored),
    }


async def _deliver_artifacts_handler(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
    user_id = ctx.user_id

    items = args.get("attachments")
    if not isinstance(items, list) or not items:
        return ToolResult(
            content="deliver_artifacts: 'attachments' must be a non-empty array",
            is_error=True,
        )
    if not ctx.session_id:
        return ToolResult(
            content="deliver_artifacts: no session context — cannot record artifacts",
            is_error=True,
        )

    try:
        delivery = await resolve_delivery_scope(user_id, ctx.session_id)
    except ScopeUnavailableError as exc:
        return ToolResult(content=f"deliver_artifacts: {exc}", is_error=True)

    # Resolved once, and OUTSIDE the unit of work below: ``owner_allowed_roots``
    # opens its own session, and nesting a second live one would have two
    # connections contending on the same SQLite file for the whole loop.
    roots = await owner_allowed_roots(user_id)
    if not roots:
        # Fail closed, but say why. An empty allowlist means the owner's managed
        # root could not be resolved at all — reporting every entry as "outside
        # your workspace" would send the model chasing its own file paths.
        logger.warning("deliver_artifacts: no allowed roots for owner %s", user_id)
        return ToolResult(
            content="deliver_artifacts: cannot resolve your workspace root — nothing was recorded",
            is_error=True,
        )

    results: list[dict[str, Any]] = []
    # One transaction for the batch — see the module docstring.
    async with async_unit_of_work() as db:
        ds = ArtifactDatastore(db)
        for raw in items:
            if not isinstance(raw, dict):
                results.append(_fail(raw, STATUS_INVALID, "entry is not an object"))
                continue
            results.append(
                await _deliver_one(
                    ds,
                    delivery,
                    raw,
                    roots=roots,
                    session_id=ctx.session_id,
                    user_id=user_id,
                )
            )

    recorded = [r for r in results if r["status"] in (STATUS_RECORDED, STATUS_UNCHANGED)]
    payload: dict[str, Any] = {"results": results, "delivered_count": len(recorded)}
    # A call that delivered nothing is surfaced as an error so the model notices
    # rather than assuming success.
    return ToolResult(content=json.dumps(payload, ensure_ascii=False), is_error=not recorded)


def build_deliver_artifacts_tool_defs() -> tuple[ToolDef, ...]:
    """Build the ``deliver_artifacts`` tool def for the host toolkit MCP server."""
    td = ToolDef(
        name=DELIVER_ARTIFACTS_TOOL_NAME,
        description=TOOL_DESCRIPTION,
        parameters=_PARAMS,
        handler=_deliver_artifacts_handler,
        read_only=False,
    )
    logger.info("Built deliver_artifacts tool def: %s", DELIVER_ARTIFACTS_TOOL_NAME)
    return (td,)
