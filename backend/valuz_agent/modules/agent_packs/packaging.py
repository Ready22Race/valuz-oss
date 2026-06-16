"""Read/write the ``.valuzpack`` archive — a zip of ``manifest.json`` plus an
optional ``skills/<slug>/`` tree for embedded (user-owned) skills.

Pure packaging: no DB, no secrets, no app state. The manifest is the contract
(``manifest.py``); this module only turns it into bytes and back, with the same
size/count caps the skill importer uses so a hostile archive can't blow up
memory or disk.
"""

from __future__ import annotations

import io
import json
import tempfile
import zipfile
from pathlib import Path

from valuz_agent.modules.agent_packs.manifest import AgentPackManifest

MANIFEST_NAME = "manifest.json"
SKILLS_DIR = "skills"

# Mirror the skill importer's caps — a pack is a small bundle of text + skill
# files, not a data dump.
_MAX_FILE_BYTES = 5 * 1024 * 1024  # 5 MiB per file
_MAX_TOTAL_BYTES = 50 * 1024 * 1024  # 50 MiB per pack (room for multi-skill teams)
_MAX_FILE_COUNT = 2048


class PackArchiveError(ValueError):
    """Raised when an uploaded archive is malformed or exceeds the caps."""


def build_archive(manifest: AgentPackManifest, skill_dirs: dict[str, Path]) -> bytes:
    """Build a ``.valuzpack`` zip in memory.

    ``skill_dirs`` maps an embedded skill slug to its on-disk source directory;
    each is written under ``skills/<slug>/``. Skills marked ``bundled`` in the
    manifest are NOT passed here (they're referenced, not carried).
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
            for path in sorted(src.rglob("*")):
                if not path.is_file():
                    continue
                rel = path.relative_to(src).as_posix()
                zf.write(path, f"{SKILLS_DIR}/{slug}/{rel}")
    return buffer.getvalue()


def _is_within(base: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def extract_archive(data: bytes) -> tuple[AgentPackManifest, Path]:
    """Parse a ``.valuzpack`` blob → (validated manifest, extracted root dir).

    The caller owns the returned temp dir (clean it up after use). Enforces the
    size/count caps and rejects path traversal (zip-slip) and a missing/invalid
    manifest. Embedded skills land under ``<root>/skills/<slug>/``.
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

    root = Path(tempfile.mkdtemp(prefix="valuz-pack-import-"))
    for info in infos:
        dest = root / info.filename
        if not _is_within(root, dest):
            raise PackArchiveError(f"unsafe path in archive: {info.filename!r}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, dest.open("wb") as out:
            out.write(src.read())

    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file():
        raise PackArchiveError("archive is missing manifest.json")
    try:
        manifest = AgentPackManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
    except (ValueError, json.JSONDecodeError) as exc:
        raise PackArchiveError(f"invalid manifest.json: {exc}") from exc

    return manifest, root


def embedded_skill_dir(root: Path, slug: str) -> Path | None:
    """Path to an extracted embedded skill, or ``None`` if absent."""
    candidate = root / SKILLS_DIR / slug
    return candidate if candidate.is_dir() else None
