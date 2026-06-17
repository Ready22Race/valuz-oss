"""User-scoped COS sync — the entry point that pushes a user's local mountable
content (projects + skills) to COS so an AGS cloud sandbox can mount it.

This is the explicit, ``user_id``-keyed counterpart to the per-session
``bind_workspace`` stage-in: given a user, enumerate the local dirs the kernel
needs inside the sandbox (real projects, managed chat cwds, the skill roots —
NOT the host's private kernel DB) and upload them to COS under a
``{user_id}/...`` prefix. The AGS sandbox tool mounts the bucket, so the kernel
sees them under ``{mount_path}/{user_id}/...``.

Invoke via the CLI: ``python -m valuz_agent.cli sync-cos``.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from valuz_agent.ports.object_store import ObjectStore

logger = logging.getLogger("valuz_agent.sandbox")


@dataclass(frozen=True)
class SyncSource:
    """One local dir to push, with the logical name it lands under in COS
    (``{user_id}/{name}/...``)."""

    name: str
    local_dir: Path


@dataclass(frozen=True)
class SyncReport:
    user_id: str
    root_prefix: str
    per_source: list[tuple[str, int, int]] = field(default_factory=list)  # (name, files, bytes)
    total_files: int = 0
    total_bytes: int = 0


def local_sync_sources() -> list[SyncSource]:
    """The local dirs to mount into a cloud sandbox — mirrors the Seatbelt
    rw-mount manifest minus the kernel's private DB (the cloud image uses its
    own). De-duplicated by realpath; only existing dirs are returned."""
    from valuz_agent.infra.config import settings
    from valuz_agent.infra.fs_registry import fs_registry as fr

    candidates: list[SyncSource] = [
        SyncSource("projects", settings.user_project_root),
        SyncSource("chats", settings.data_dir / "projects"),
        SyncSource("skills/official", fr.official_skill_root()),
        SyncSource("skills/claude", fr.user_skill_root("claude")),
    ]
    for d in fr.legacy_user_skill_roots():
        candidates.append(SyncSource(f"skills/legacy/{Path(d).name}", Path(d)))

    seen: set[str] = set()
    out: list[SyncSource] = []
    for s in candidates:
        if not s.local_dir.is_dir():
            continue
        real = os.path.realpath(str(s.local_dir))
        if real in seen:
            continue
        seen.add(real)
        out.append(s)
    return out


async def sync_local_to_cos(
    user_id: str,
    *,
    store: ObjectStore,
    sources: list[SyncSource] | None = None,
) -> SyncReport:
    """Upload each source dir to ``{user_id}/{name}/...`` in ``store``. Returns a
    per-source report. Caps come from ``VALUZ_AGS_STAGE_MAX_*`` (per source)."""
    from valuz_agent.infra.config import settings
    from valuz_agent.integrations.object_store_s3 import stage_directory

    srcs = sources if sources is not None else local_sync_sources()
    per_source: list[tuple[str, int, int]] = []
    total_files = 0
    total_bytes = 0
    for s in srcs:
        prefix = f"{user_id}/{s.name}"
        n, b = await stage_directory(
            store,
            prefix,
            str(s.local_dir),
            max_files=settings.ags_stage_max_files,
            max_bytes=settings.ags_stage_max_bytes,
        )
        per_source.append((s.name, n, b))
        total_files += n
        total_bytes += b
        logger.info("cos-sync: %s → %s (%d files, %d bytes)", s.local_dir, prefix, n, b)
    return SyncReport(
        user_id=user_id,
        root_prefix=user_id,
        per_source=per_source,
        total_files=total_files,
        total_bytes=total_bytes,
    )
