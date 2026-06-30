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
from valuz_agent.api.deps import get_current_user_id
from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.sessions.datastore import SessionDatastore

logger = logging.getLogger(__name__)

DELIVER_ARTIFACTS_TOOL_NAME = "deliver_artifacts"

TOOL_DESCRIPTION = (
    "Deliver finished output files as session artifacts — they appear in the "
    "user's '生成文件' (Generated Files) panel. Call this when you have produced "
    "deliverables (reports, HTML, documents, data files, …) the user should be "
    "able to find and open. Pass an 'attachments' array; each entry needs a "
    "'filePath' (absolute path to a file you already wrote). 'fileName', "
    "'fileSize' and 'mimeType' are optional — they are derived from the file on "
    "disk when omitted. Re-delivering the same path updates the existing entry "
    "instead of duplicating it."
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


async def _deliver_artifacts_handler(args: dict[str, Any], ctx: ExecContext, user_id: str | None = None) -> ToolResult:
    if user_id is None:
        raise ValueError("user_id is required")

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
