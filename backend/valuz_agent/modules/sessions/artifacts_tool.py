"""``deliver_artifacts`` in-process MCP tool.

A single built-in tool the agent calls to declare finished outputs ("生成文件").
Registered in the host toolkit MCP ``base`` toolset (see ``boot/steps.py``), so
it is loaded into **every** session and is runtime-agnostic
(claude / codex / deepagents), surfacing to models as
``mcp__harness__deliver_artifacts``.

It is the inverse of the upload pipeline: uploads are files the *user* hands the
agent (``valuz_session_attachment``, staged per turn); delivered artifacts are
files the *agent* produced and marks as deliverables
(``valuz_session_artifact``, durable). The session panel renders them as a
curated, read-only list the user can open.

Input mirrors the reference payload — an ``attachments`` array of
``{filePath, fileName?, fileSize?, mimeType?}``. Only ``filePath`` is required;
``fileName`` / ``fileSize`` / ``mimeType`` are derived from disk when omitted, so
the agent can deliver with just a path. The handler reads the *current* file
from disk, so a missing file is skipped (reported back) rather than recorded.

Owner boundary
--------------
``filePath`` is model-supplied, so it is checked against the caller's own roots
(``owner_allowed_roots`` + ``assert_owned`` — the same isolation line the
``/v1/files/resolve`` endpoint uses, symlink-escape guard included) before
anything else touches it. Without that check a model could register another
tenant's absolute path: harmless-looking today because every *read* path
re-validates ownership, but the row itself is already a cross-owner reference,
and any future handling that copies bytes host-side (content snapshots) would
turn it into a real leak — the host process can see the whole shared mount.
The check runs BEFORE the ``isfile`` probe so an out-of-bounds path cannot be
used as an existence oracle for someone else's files.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
from pathlib import Path
from typing import Any

from src.core import ToolDef, ToolResult
from src.core.tools import ExecContext

import valuz_agent.boot.kernel  # noqa: F401  (sets kernel import path)
from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.files.service import assert_owned, owner_allowed_roots
from valuz_agent.modules.sessions.datastore import SessionDatastore

logger = logging.getLogger(__name__)

DELIVER_ARTIFACTS_TOOL_NAME = "deliver_artifacts"

# Skip reason for a path outside the caller's roots. Phrased as an instruction
# because the model only ever sees this as text (the toolkit MCP renders tool
# errors as a string prefix, not a wire failure), so it has to say what to do
# next — not just that something was refused.
_NOT_OWNED_REASON = (
    "path is outside your workspace — write the file into your working "
    "directory and deliver it from there"
)

TOOL_DESCRIPTION = (
    "Register finished output files as session artifacts — they show up in the "
    "user's '生成文件' (Generated Files) panel as a curated, read-only list the "
    "user can open. Pass an 'attachments' array; each entry needs a 'filePath' "
    "(absolute path to a file you already wrote). 'fileName', 'fileSize' and "
    "'mimeType' are optional — they are derived from the file on disk when "
    "omitted. The file must live inside your own working directory; paths "
    "outside it are refused. Re-delivering the same path updates the existing "
    "entry instead of duplicating it. When you mention a delivered file in your "
    "reply text, link "
    "it by joining `valuz-file://` with the absolute filePath (which starts with "
    "`/`), giving three slashes — e.g. "
    "[report.md](valuz-file:///Users/you/proj/report.md) — so the client can open "
    "it (it resolves to a local path or a signed URL depending on where the file "
    "lives)."
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
                        "description": "Absolute path to a file you already wrote.",
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
                },
                "required": ["filePath"],
            },
            "minItems": 1,
        }
    },
    "required": ["attachments"],
}


def _coerce_size(raw: Any) -> int | None:
    """Best-effort int from a model-supplied ``fileSize`` (may be str/float)."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw if raw >= 0 else None
    if isinstance(raw, float):
        return int(raw) if raw >= 0 else None
    if isinstance(raw, str) and raw.strip().isdigit():
        return int(raw.strip())
    return None


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

    # Resolved once per call, and OUTSIDE the unit of work below:
    # ``owner_allowed_roots`` opens its own session, and nesting a second live
    # session inside the handler's would have two connections contending on the
    # same sqlite file for the whole loop.
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

    delivered: list[str] = []
    skipped: list[dict[str, str]] = []

    async with async_unit_of_work() as db:
        ds = SessionDatastore(db)
        for raw in items:
            if not isinstance(raw, dict):
                skipped.append({"filePath": str(raw), "reason": "entry is not an object"})
                continue
            file_path = raw.get("filePath")
            if not file_path or not isinstance(file_path, str):
                skipped.append({"filePath": str(file_path), "reason": "missing 'filePath'"})
                continue
            abs_path = os.path.abspath(os.path.expanduser(file_path))
            # Owner boundary first — see the module docstring. The row keeps the
            # unresolved ``abs_path``; ``assert_owned``'s resolved form is only
            # the gate, so a legitimate delivery records exactly the path the
            # agent wrote (the read path resolves symlinks again anyway).
            try:
                assert_owned(Path(abs_path), roots)
            except PermissionError:
                logger.warning(
                    "deliver_artifacts: refused out-of-bounds path for owner %s", user_id
                )
                skipped.append({"filePath": file_path, "reason": _NOT_OWNED_REASON})
                continue
            if not os.path.isfile(abs_path):
                skipped.append({"filePath": file_path, "reason": "file not found"})
                continue

            file_name = raw.get("fileName") or Path(abs_path).name
            file_size = _coerce_size(raw.get("fileSize"))
            if file_size is None:
                try:
                    file_size = os.stat(abs_path).st_size
                except OSError:
                    file_size = 0
            mime_type = raw.get("mimeType") or mimetypes.guess_type(file_name)[0]

            await ds.upsert_artifact(
                user_id,
                ctx.session_id,
                file_path=abs_path,
                file_name=str(file_name),
                file_size=int(file_size),
                mime_type=mime_type,
            )
            delivered.append(str(file_name))

    payload: dict[str, Any] = {"delivered": delivered, "delivered_count": len(delivered)}
    if skipped:
        payload["skipped"] = skipped
    # A call that delivered nothing (every entry skipped) is surfaced as an
    # error so the model notices the paths were wrong instead of silently
    # assuming success.
    is_error = not delivered
    return ToolResult(content=json.dumps(payload, ensure_ascii=False), is_error=is_error)


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
