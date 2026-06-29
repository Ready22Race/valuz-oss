"""Read/write the ``.valuzpack`` archive — a zip of ``manifest.json`` plus an
optional ``skills/<slug>/`` tree for embedded (user-owned) skills.

Pure packaging: no DB, no secrets, no app state. The manifest is the contract
(``manifest.py``); this module only turns it into bytes and back, with the same
size/count caps the skill importer uses so a hostile archive can't blow up
memory or disk.

Skill slugs are sanitized to a single safe path segment on the way in and out:
some installs stored a skill's slug as a full path (e.g. Windows
``C:/Users/x/.agents/skills/price-audit`` — note the drive letter — or a POSIX
``/home/x/.../price-audit``). Embedding that verbatim produced archive entries
like ``skills/C:/Users/.../SKILL.md`` that tripped the zip-slip guard on Windows
(the drive letter re-anchors the resolved path and escapes the temp root) and
silently mis-nested elsewhere (``skills//Users/...`` → ``skills/Users/...``), so
the recipient could never find the skill. Both the writer and the reader now
collapse such a slug to its trailing component, and the reader rewrites a legacy
manifest's slugs to match so already-exported packs still import.
"""

from __future__ import annotations

import io
import json
import re
import tempfile
import zipfile
from pathlib import Path, PureWindowsPath

from valuz_agent.modules.agent_packs.manifest import AgentPackManifest

MANIFEST_NAME = "manifest.json"
SKILLS_DIR = "skills"

# Mirror the skill importer's caps — a pack is a small bundle of text + skill
# files, not a data dump.
_MAX_FILE_BYTES = 5 * 1024 * 1024  # 5 MiB per file
_MAX_TOTAL_BYTES = 50 * 1024 * 1024  # 50 MiB per pack (room for multi-skill teams)
_MAX_FILE_COUNT = 2048

_SLUG_FALLBACK_RE = re.compile(r"[^A-Za-z0-9._-]+")
# A bare drive letter segment like ``C:`` (any platform's archive entry).
_DRIVE_RE = re.compile(r"[A-Za-z]:")


class PackArchiveError(ValueError):
    """Raised when an uploaded archive is malformed or exceeds the caps."""


def sanitize_skill_slug(slug: str) -> str:
    """Reduce a possibly path-shaped skill slug to one safe archive segment.

    ``PureWindowsPath`` parses both ``/`` and ``\\`` separators *and* drive
    letters on any host OS, so its ``.name`` is the trailing component whether
    the slug came from Windows (``C:/Users/x/price-audit``,
    ``C:\\Users\\x\\price-audit``), POSIX (``/home/x/price-audit``), or is
    already a clean slug (``price-audit`` → unchanged). Degenerate inputs (a
    bare drive, ``..``, empty) fall back to a character-scrubbed form so the
    result is always a single, non-empty, separator-free segment.
    """
    name = PureWindowsPath(str(slug)).name
    if not name or name in (".", ".."):
        name = _SLUG_FALLBACK_RE.sub("-", str(slug)).strip("-._")
    return name or "skill"


def build_archive(manifest: AgentPackManifest, skill_dirs: dict[str, Path]) -> bytes:
    """Build a ``.valuzpack`` zip in memory.

    ``skill_dirs`` maps an embedded skill slug to its on-disk source directory;
    each is written under ``skills/<slug>/`` (the slug sanitized to a safe
    segment). Skills marked ``bundled`` in the manifest are NOT passed here
    (they're referenced, not carried).
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
    return buffer.getvalue()


def _is_within(base: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def _scrub_segments(posix: str) -> list[str] | None:
    """Split a ``/``-joined path into safe segments.

    Drops empty / ``.`` segments; returns ``None`` if a ``..`` traversal or a
    bare drive-letter segment is present (a genuine escape attempt). An
    all-empty input yields ``[]`` (safe, nothing to add).
    """
    parts: list[str] = []
    for seg in posix.split("/"):
        if not seg or seg == ".":
            continue
        if seg == ".." or _DRIVE_RE.fullmatch(seg):
            return None
        parts.append(seg)
    return parts


def _member_relpath(
    name: str, embedded: list[str], slug_map: dict[str, str]
) -> str | None:
    """Map an archive entry name to a safe relative path under the extract root.

    Embedded-skill files live under ``skills/<slug>/...``; a legacy export may
    carry a path-shaped ``<slug>`` (drive letter / leading slash / backslashes),
    so collapse it to its sanitized segment using the manifest's known slugs
    (longest first). Returns ``None`` for a genuine traversal attempt.
    """
    posix = str(name).replace("\\", "/")
    prefix = f"{SKILLS_DIR}/"
    if posix.startswith(prefix):
        rest = posix[len(prefix) :]
        for raw in embedded:  # longest raw slug first → most specific match wins
            rawp = str(raw).replace("\\", "/")
            if rest == rawp or rest.startswith(rawp + "/"):
                tail = _scrub_segments(rest[len(rawp) :])
                if tail is None:
                    return None
                return "/".join([SKILLS_DIR, slug_map[raw], *tail])
    segs = _scrub_segments(posix)
    return "/".join(segs) if segs else None


def _normalize_manifest_slugs(
    manifest: AgentPackManifest, slug_map: dict[str, str]
) -> AgentPackManifest:
    """Rewrite a manifest's embedded-skill slugs (index + agent references) to
    their sanitized form, so the importer finds the extracted skills and the
    imported agents reference them. No-op when every slug is already clean."""
    if not any(raw != clean for raw, clean in slug_map.items()):
        return manifest
    skills = [
        s.model_copy(update={"slug": slug_map.get(s.slug, s.slug)})
        for s in manifest.skills
    ]
    agents = [
        a.model_copy(update={"skills": [slug_map.get(x, x) for x in a.skills]})
        for a in manifest.agents
    ]
    return manifest.model_copy(update={"skills": skills, "agents": agents})


def extract_archive(data: bytes) -> tuple[AgentPackManifest, Path]:
    """Parse a ``.valuzpack`` blob → (validated manifest, extracted root dir).

    The caller owns the returned temp dir (clean it up after use). Enforces the
    size/count caps, rejects path traversal (zip-slip), and normalizes legacy
    path-shaped embedded-skill slugs so an already-exported (malformed) pack
    still lands its skills under ``<root>/skills/<slug>/``.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise PackArchiveError("not a valid .valuzpack archive (bad zip)") from exc

    infos = [i for i in zf.infolist() if not i.is_dir()]
    if len(infos) > _MAX_FILE_COUNT:
        raise PackArchiveError(f"archive exceeds the {_MAX_FILE_COUNT}-file limit")
    total = 0
    for info in infos:
        if info.file_size > _MAX_FILE_BYTES:
            raise PackArchiveError(f"file {info.filename!r} exceeds the per-file size limit")
        total += info.file_size
        if total > _MAX_TOTAL_BYTES:
            raise PackArchiveError("archive exceeds the total size limit")

    # Parse the manifest up front (it's the contract) so legacy path-shaped
    # embedded-skill slugs can be normalized while extracting — the returned
    # manifest and the on-disk tree then agree.
    try:
        raw_manifest = zf.read(MANIFEST_NAME)
    except KeyError as exc:
        raise PackArchiveError("archive is missing manifest.json") from exc
    try:
        manifest = AgentPackManifest.model_validate_json(raw_manifest.decode("utf-8"))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PackArchiveError(f"invalid manifest.json: {exc}") from exc

    embedded = sorted(
        {s.slug for s in manifest.skills if s.source == "embedded"},
        key=len,
        reverse=True,
    )
    slug_map = {s: sanitize_skill_slug(s) for s in embedded}

    root = Path(tempfile.mkdtemp(prefix="valuz-pack-import-"))
    for info in infos:
        rel = _member_relpath(info.filename, embedded, slug_map)
        dest = root / rel if rel is not None else None
        if dest is None or not _is_within(root, dest):
            raise PackArchiveError(f"unsafe path in archive: {info.filename!r}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, dest.open("wb") as out:
            out.write(src.read())

    return _normalize_manifest_slugs(manifest, slug_map), root


def embedded_skill_dir(root: Path, slug: str) -> Path | None:
    """Path to an extracted embedded skill, or ``None`` if absent."""
    candidate = root / SKILLS_DIR / slug
    return candidate if candidate.is_dir() else None
