"""Read/write the ``.valuz-project`` archive — a zip of ``manifest.json``
plus an optional ``skills/<slug>/`` tree for embedded (user-owned) skills
and an optional ``memory/`` tree carrying the project's on-disk memory
directory.

Pure packaging: no DB, no secrets, no app state. The manifest is the
contract (``manifest.py``); this module only turns it into bytes and back,
mirroring ``agent_packs.packaging`` for the zip-slip / size-cap defenses
but allowing a ``memory/`` top-level prefix alongside ``skills/``.
"""

from __future__ import annotations

import io
import json
import re
import tempfile
import zipfile
from pathlib import Path, PureWindowsPath
from typing import Any

from valuz_agent.modules.project_packs.manifest import ProjectPackManifest

MANIFEST_NAME = "manifest.json"
SKILLS_DIR = "skills"
MEMORY_DIR = "memory"

# mirror agent_packs.packaging
_MAX_FILE_BYTES = 5 * 1024 * 1024  # 5 MiB per file
_MAX_TOTAL_BYTES = 50 * 1024 * 1024  # 50 MiB per pack
_MAX_FILE_COUNT = 2048

_SLUG_FALLBACK_RE = re.compile(r"[^A-Za-z0-9._-]+")
# A bare drive letter segment like ``C:`` (any platform's archive entry).
_DRIVE_RE = re.compile(r"[A-Za-z]:")


class ProjectPackArchiveError(ValueError):
    """Raised when an uploaded project archive is malformed or exceeds caps."""


def sanitize_skill_slug(slug: str) -> str:
    """Reduce a possibly path-shaped skill slug to one safe archive segment.

    Re-exported here from agent_packs so project_packaging callers don't
    need to import agent_packs. Pure mirror.
    """
    name = PureWindowsPath(str(slug)).name
    if not name or name in (".", ".."):
        name = _SLUG_FALLBACK_RE.sub("-", str(slug)).strip("-._")
    return name or "skill"


def build_project_archive(
    manifest: ProjectPackManifest,
    skill_dirs: dict[str, Path],
    memory_dir: Path | None,
) -> bytes:
    """Build a ``.valuz-project`` zip in memory.

    ``skill_dirs`` maps an embedded skill slug to its on-disk source dir;
    each is written under ``skills/<slug>/`` (the slug sanitized to a safe
    segment). ``memory_dir`` (optional) is written under ``memory/``
    preserving its relative tree. Skills marked ``bundled`` are NOT passed
    here (they're referenced, not carried).
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            MANIFEST_NAME,
            manifest.model_dump_json(indent=2, exclude_none=True),
        )
        for slug, src in skill_dirs.items():
            if not src.is_dir():
                continue
            safe = sanitize_skill_slug(slug)
            for path in sorted(src.rglob("*")):
                if not path.is_file():
                    continue
                rel = path.relative_to(src).as_posix()
                zf.write(path, f"{SKILLS_DIR}/{safe}/{rel}")
        if memory_dir is not None and memory_dir.is_dir():
            for path in sorted(memory_dir.rglob("*")):
                if not path.is_file():
                    continue
                rel = path.relative_to(memory_dir).as_posix()
                zf.write(path, f"{MEMORY_DIR}/{rel}")
    return buffer.getvalue()


# mirror agent_packs.packaging private helpers


def _is_within(base: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def _scrub_segments(posix: str) -> list[str] | None:
    """Split a ``/``-joined path into safe segments.

    Drops empty / ``.`` segments; returns ``None`` on ``..`` traversal or a
    bare drive-letter segment (escape attempt). An all-empty input yields
    ``[]``. Mirrors ``agent_packs.packaging._scrub_segments``.
    """
    parts: list[str] = []
    for seg in posix.split("/"):
        if not seg or seg == ".":
            continue
        if seg == ".." or _DRIVE_RE.fullmatch(seg):
            return None
        parts.append(seg)
    return parts


def _member_relpath(name: str, embedded: list[str], slug_map: dict[str, str]) -> str | None:
    """Map an archive entry to a safe relative path under the extract root.

    Allows the top-level ``skills/``, ``memory/``, and the bare manifest;
    rewrites legacy path-shaped embedded-skill slugs the same way
    ``agent_packs.packaging`` does. Returns ``None`` for a genuine
    traversal attempt.
    """
    posix = str(name).replace("\\", "/")
    # Skills prefix — sanitize slug like agent_packs does.
    skills_prefix = f"{SKILLS_DIR}/"
    if posix.startswith(skills_prefix):
        rest = posix[len(skills_prefix) :]
        for raw in embedded:  # longest raw slug first
            rawp = str(raw).replace("\\", "/")
            if rest == rawp or rest.startswith(rawp + "/"):
                tail = _scrub_segments(rest[len(rawp) :])
                if tail is None:
                    return None
                return "/".join([SKILLS_DIR, slug_map[raw], *tail])
        # Fall through to default scrubbing (handles a clean slug too).
    memory_prefix = f"{MEMORY_DIR}/"
    if posix.startswith(memory_prefix):
        tail = _scrub_segments(posix[len(memory_prefix) :])
        return "/".join([MEMORY_DIR, *tail]) if tail is not None else None
    segs = _scrub_segments(posix)
    return "/".join(segs) if segs else None


def _normalize_manifest_slugs(
    manifest: ProjectPackManifest, slug_map: dict[str, str]
) -> ProjectPackManifest:
    """Rewrite embedded-skill slugs (skills index + member agent references)
    to their sanitized form, mirroring ``agent_packs.packaging``."""
    if not any(raw != clean for raw, clean in slug_map.items()):
        return manifest
    skills = [s.model_copy(update={"slug": slug_map.get(s.slug, s.slug)}) for s in manifest.skills]
    members: list[Any] = []
    for m in manifest.members:
        agent = m.agent.model_copy(update={"skills": [slug_map.get(x, x) for x in m.agent.skills]})
        members.append(m.model_copy(update={"agent": agent}))
    return manifest.model_copy(update={"skills": skills, "members": members})


def extract_project_archive(data: bytes) -> tuple[ProjectPackManifest, Path]:
    """Parse a ``.valuz-project`` blob → (validated manifest, extracted
    root dir). The caller owns the returned temp dir (clean it up after
    use). Enforces size/count caps, rejects path traversal (zip-slip), and
    normalizes legacy path-shaped embedded-skill slugs.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ProjectPackArchiveError("not a valid .valuz-project archive (bad zip)") from exc

    infos = [i for i in zf.infolist() if not i.is_dir()]
    if len(infos) > _MAX_FILE_COUNT:
        raise ProjectPackArchiveError(f"archive exceeds the {_MAX_FILE_COUNT}-file limit")
    total = 0
    for info in infos:
        if info.file_size > _MAX_FILE_BYTES:
            raise ProjectPackArchiveError(f"file {info.filename!r} exceeds the per-file size limit")
        total += info.file_size
        if total > _MAX_TOTAL_BYTES:
            raise ProjectPackArchiveError("archive exceeds the total size limit")

    try:
        raw_manifest = zf.read(MANIFEST_NAME)
    except KeyError as exc:
        raise ProjectPackArchiveError("archive is missing manifest.json") from exc
    try:
        manifest = ProjectPackManifest.model_validate_json(raw_manifest.decode("utf-8"))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ProjectPackArchiveError(f"invalid manifest.json: {exc}") from exc

    embedded = sorted(
        {s.slug for s in manifest.skills if s.source == "embedded"},
        key=len,
        reverse=True,
    )
    slug_map = {s: sanitize_skill_slug(s) for s in embedded}

    root = Path(tempfile.mkdtemp(prefix="valuz-project-import-"))
    for info in infos:
        if info.filename == MANIFEST_NAME:
            continue  # already parsed; don't write to disk
        rel = _member_relpath(info.filename, embedded, slug_map)
        dest = root / rel if rel is not None else None
        if dest is None or not _is_within(root, dest):
            raise ProjectPackArchiveError(f"unsafe path in archive: {info.filename!r}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, dest.open("wb") as out:
            out.write(src.read())

    return _normalize_manifest_slugs(manifest, slug_map), root


def embedded_skill_dir(root: Path, slug: str) -> Path | None:
    """Path to an extracted embedded skill, or ``None`` if absent."""
    candidate = root / SKILLS_DIR / slug
    return candidate if candidate.is_dir() else None


def memory_root(root: Path) -> Path | None:
    """Path to the extracted memory dir, or ``None`` if absent."""
    candidate = root / MEMORY_DIR
    return candidate if candidate.is_dir() else None
