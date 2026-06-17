"""User-scoped COS sync — pushes a user's local mountable content (projects +
skills) to COS so an AGS cloud sandbox can mount it.

This is the explicit, ``user_id``-keyed counterpart to the per-session
``bind_workspace`` stage-in: given a user, enumerate the local dirs the kernel
needs inside the sandbox (real projects, managed chat cwds, the skill roots —
NOT the host's private kernel DB) and upload them to COS.

**Layout = prefix-preserving** (see ``sandbox_paths``): each dir lands at COS
key ``{user_id}{realpath}`` — i.e. the host's absolute path mirrored under the
user prefix. The AGS tool mounts ``{user_id}/`` at ``ags_mount_path``, so the
kernel sees each dir at ``{mount_path}{realpath}`` — the exact path the kernel
seam projects cwd and skill dirs onto. One layout, shared by sync, cwd staging,
and skill translation.

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
    """One local dir to push. ``name`` is a human label for logs only — the COS
    key is derived from the dir's absolute realpath (prefix-preserving, see
    ``sandbox_paths.cos_key_for``) so it lines up with the in-sandbox mount path
    used for cwd and skill translation. ``is_skill`` marks the skill roots so a
    cloud kernel can pre-sync just those (cwds are staged per-session)."""

    name: str
    local_dir: Path
    is_skill: bool = False


@dataclass(frozen=True)
class SyncReport:
    user_id: str
    root_prefix: str
    per_source: list[tuple[str, int, int]] = field(default_factory=list)  # (name, files, bytes)
    total_files: int = 0
    total_bytes: int = 0


def _builtin_skills_root() -> Path:
    """The package's bundled skills dir (``valuz-project-docs``, ``skill-creator``
    parent) — ships inside the kernel image, but a cloud kernel resolves the
    host's absolute path under the mount, so it must be synced like any other
    skill root."""
    import valuz_agent

    return Path(valuz_agent.__file__).resolve().parent / "resources" / "builtin_skills"


def local_sync_sources() -> list[SyncSource]:
    """The local dirs to mount into a cloud sandbox — projects, managed chat
    cwds, and every skill root (builtin / official / user / legacy). The
    kernel's private DB is excluded (the cloud image uses its own).
    De-duplicated by realpath; only existing dirs are returned."""
    from valuz_agent.infra.config import settings
    from valuz_agent.infra.fs_registry import fs_registry as fr

    candidates: list[SyncSource] = [
        SyncSource("projects", settings.user_project_root),
        SyncSource("chats", settings.data_dir / "projects"),
        SyncSource("skills/builtin", _builtin_skills_root(), is_skill=True),
        SyncSource("skills/official", fr.official_skill_root(), is_skill=True),
        SyncSource("skills/claude", fr.user_skill_root("claude"), is_skill=True),
    ]
    for d in fr.legacy_user_skill_roots():
        candidates.append(SyncSource(f"skills/legacy/{Path(d).name}", Path(d), is_skill=True))

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


def skill_sync_sources() -> list[SyncSource]:
    """Just the skill roots — what a cloud kernel needs pre-synced before a
    session can materialize skills (cwds are staged per-session by
    ``bind_workspace``)."""
    return [s for s in local_sync_sources() if s.is_skill]


async def sync_local_to_cos(
    user_id: str,
    *,
    store: ObjectStore,
    sources: list[SyncSource] | None = None,
) -> SyncReport:
    """Upload each source dir to COS key ``{user_id}{realpath}/...``. Returns a
    per-source report. Caps come from ``VALUZ_AGS_STAGE_MAX_*`` (per source)."""
    from valuz_agent.infra.config import settings
    from valuz_agent.integrations.object_store_s3 import stage_directory
    from valuz_agent.integrations.sandbox_paths import cos_key_for

    srcs = sources if sources is not None else local_sync_sources()
    per_source: list[tuple[str, int, int]] = []
    total_files = 0
    total_bytes = 0
    for s in srcs:
        real = os.path.realpath(str(s.local_dir))
        prefix = cos_key_for(real, user_id)
        n, b = await stage_directory(
            store,
            prefix,
            real,
            max_files=settings.ags_stage_max_files,
            max_bytes=settings.ags_stage_max_bytes,
        )
        per_source.append((s.name, n, b))
        total_files += n
        total_bytes += b
        logger.info("cos-sync: %s → %s (%d files, %d bytes)", real, prefix, n, b)
    return SyncReport(
        user_id=user_id,
        root_prefix=user_id,
        per_source=per_source,
        total_files=total_files,
        total_bytes=total_bytes,
    )


async def sync_skills_best_effort() -> None:
    """Push the skill roots to COS for the local user so a cloud kernel can
    resolve them under the mount. Best-effort: a no-op (logged) when COS is
    unconfigured, and never raises — a sync failure must not block boot.
    """
    try:
        from valuz_agent.infra.local_identity import resolve_local_user_id
        from valuz_agent.integrations.object_store_s3 import cos_object_store

        store = cos_object_store()
        if store is None:
            logger.info("skill sync skipped: COS not configured")
            return
        report = await sync_local_to_cos(
            resolve_local_user_id(), store=store, sources=skill_sync_sources()
        )
        logger.info(
            "provision skill sync: %d files (%d bytes) for user %s",
            report.total_files,
            report.total_bytes,
            report.user_id,
        )
    except Exception:  # noqa: BLE001 — best-effort; skills just won't be available
        logger.warning(
            "provision skill sync failed — skills may be unavailable in the cloud kernel",
            exc_info=True,
        )
