"""Sync bundled official skills from package resources to the user's official skills directory.

Each bundled skill ships with a `.bundled-version` marker file containing a content
hash of the vendored tree. On startup we compare that hash against the destination's
marker; on mismatch (or missing destination) we copy/overwrite. User-added files
under the destination root that aren't part of the bundled tree are left alone —
we only manage paths that exist upstream.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from collections.abc import Iterable
from pathlib import Path

from valuz_agent.infra.fs_registry import fs_registry

logger = logging.getLogger(__name__)

BUNDLED_VERSION_FILE = ".bundled-version"


def _resources_root() -> Path:
    """Path to backend/valuz_agent/resources/official_skills/ in the source tree."""
    return Path(__file__).resolve().parent.parent / "resources" / "official_skills"


def _builtin_resources_root() -> Path:
    """Path to backend/valuz_agent/resources/builtin_skills/ (valuz-project-docs, browser).

    Builtin skills are materialized ALONGSIDE official skills into the per-user
    official-skills dir (same landing root, no separate directory). This is what
    lets a remote kernel — running inside a sandbox that mounts the user's
    official-skills subtree, not the host package tree — resolve their absolute
    source paths. ``capability_resolver.project_docs_skill_dir`` /
    ``browser_skill_dir`` return those materialized locations.
    """
    return Path(__file__).resolve().parent.parent / "resources" / "builtin_skills"


def _user_official_skills_root(user_id: str) -> Path:
    """Bundled-skill landing root. Delegated to ``fs_registry`` so the
    bootstrap and the discovery source (`OfficialSkillSource`) always
    agree on the location. Default is ``<data_dir>/official-skills/``."""
    return fs_registry.official_skill_root(user_id=user_id)


def _hash_directory(root: Path) -> str:
    """Stable content hash of all files under root, excluding the marker file itself."""
    h = hashlib.sha256()
    files = sorted(p for p in root.rglob("*") if p.is_file() and p.name != BUNDLED_VERSION_FILE)
    for path in files:
        rel = path.relative_to(root).as_posix()
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def _list_bundled_skill_dirs(resources_root: Path) -> list[Path]:
    if not resources_root.exists():
        return []
    return [
        p for p in sorted(resources_root.iterdir()) if p.is_dir() and not p.name.startswith("_")
    ]


def _copy_skill(src: Path, dest: Path, version_hash: str) -> None:
    if dest.exists():
        # Wipe and re-copy. Bundled skills are managed artifacts; users who want to
        # tweak one should "Copy" it into the user scope first instead of editing in place.
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    (dest / BUNDLED_VERSION_FILE).write_text(version_hash, encoding="utf-8")


def sync_bundled_official_skills(user_id: str) -> list[str]:
    """Idempotent sync. Returns the list of skill slugs that were (re-)installed.

    Strategy:
      - For each subdirectory under resources/official_skills/:
          - Compute content hash of the source directory.
          - If destination directory does not exist OR its `.bundled-version`
            marker disagrees, wipe and re-copy.
          - Otherwise leave it alone (idempotent fast path).
      - Errors on individual skills are logged but do not abort the loop —
        a single bad bundle should not prevent the app from starting.
    """
    dest_root = _user_official_skills_root(user_id)
    dest_root.mkdir(parents=True, exist_ok=True)

    # Official skills (skill-creator, …) and builtin skills (valuz-project-docs,
    # browser) land in the SAME per-user root — builtin skills are not given a
    # separate directory. Slugs never collide across the two source trees.
    src_skills = _list_bundled_skill_dirs(_resources_root()) + _list_bundled_skill_dirs(
        _builtin_resources_root()
    )

    installed: list[str] = []
    for src_skill in src_skills:
        slug = src_skill.name
        dest_skill = dest_root / slug
        try:
            version_hash = _hash_directory(src_skill)
            existing_marker = dest_skill / BUNDLED_VERSION_FILE
            if dest_skill.exists() and existing_marker.exists():
                if existing_marker.read_text(encoding="utf-8").strip() == version_hash:
                    continue  # up to date
            _copy_skill(src_skill, dest_skill, version_hash)
            installed.append(slug)
            logger.info("synced bundled official skill: %s", slug)
        except Exception:  # noqa: BLE001 — best-effort startup sync
            logger.exception("failed to sync bundled official skill: %s", slug)

    return installed


def is_bundled_skill(skill_dir: Path) -> bool:
    """True if the skill directory carries our bundled-version marker."""
    return (skill_dir / BUNDLED_VERSION_FILE).is_file()


def _template_skills_root() -> Path:
    """Path to ``backend/valuz_agent/resources/template_skills/``.

    These are bundled skills that ship *with an agent-team template* (the
    investment / Xiaohongshu / World Cup rosters). Unlike ``official_skills/``,
    they are NOT synced for everyone at boot — they'd clutter the library with
    skills no agent uses yet. They land on demand when the template is added
    (see ``materialize_template_skills``)."""
    return Path(__file__).resolve().parent.parent / "resources" / "template_skills"


def materialize_template_skills(
    slugs: Iterable[str],
    *,
    user_id: str,
) -> list[str]:
    """Copy the named template skills into the user's official-skills dir.

    Same idempotent marker logic as :func:`sync_bundled_official_skills`, so a
    skill an agent team brings in lands in the library *and* resolves at session
    time. Slugs not shipped under ``template_skills/`` are skipped. Returns the
    slugs that were (re-)installed.
    """
    src_root = _template_skills_root()
    dest_root = _user_official_skills_root(user_id)
    dest_root.mkdir(parents=True, exist_ok=True)

    installed: list[str] = []
    for slug in slugs:
        src = src_root / slug
        if not src.is_dir():
            continue
        dest = dest_root / slug
        try:
            version_hash = _hash_directory(src)
            existing_marker = dest / BUNDLED_VERSION_FILE
            if dest.exists() and existing_marker.exists():
                if existing_marker.read_text(encoding="utf-8").strip() == version_hash:
                    continue  # already up to date
            _copy_skill(src, dest, version_hash)
            installed.append(slug)
            logger.info("materialized template skill: %s", slug)
        except Exception:  # noqa: BLE001 — best-effort, one bad skill shouldn't sink the add
            logger.exception("failed to materialize template skill: %s", slug)
    return installed
