"""Materialize Session.skills into a project's cwd.

The Open Agent Skills standard places skill packs under ``cwd/.agents/skills/``;
DeepAgents and Codex both read this layout natively, so they share one subtree.
Claude Agent SDK keeps its own ``cwd/.claude/skills/`` discovery path and
therefore needs a parallel materializer.

**Link, not copy (per platform).** Each managed entry points at the absolute
source directory so edits the user makes to the source skill files are visible
to the running runtime *immediately* — no need to re-create the session or call
this module again. The link primitive is chosen per platform:

- **POSIX** — a symlink.
- **Windows** — a *directory junction*. A plain ``os.symlink`` on Windows needs
  Developer Mode or admin rights (``WinError 1314`` otherwise), which ordinary
  users don't have; a junction needs no elevation and reflects live edits just
  like a symlink. Falls back to a one-time *copy* only when junctions are
  unsupported (cwd on FAT/exFAT, a network share, or a UNC source). A copy does
  NOT track later edits — it is the last resort, not the norm.

Two consequences worth surfacing:

1. Live edits to the source are reflected for symlink/junction entries (see above).
2. Deleting the source after materialize leaves a broken symlink/junction under
   the skills root. ``_remove_managed_entry`` handles this case (``os.path.islink``
   / ``os.path.isjunction`` both return True for broken links) so cleanup is
   still idempotent.

Cleanup is manifest-driven: only entries we wrote during a previous session
are eligible for removal, and the manifest records each entry's *kind* so a
copied directory is removed precisely (``shutil.rmtree``) while a user-placed
real directory of the same name is never touched. Anything the user hand-placed
under the skills root is sacred.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass

logger = logging.getLogger(__name__)


CLAUDE_SKILLS_SUBDIR = ".claude/skills"
CLAUDE_MANIFEST = ".claude/.harness-skills.json"
AGENTS_SKILLS_SUBDIR = ".agents/skills"
AGENTS_MANIFEST = ".agents/.harness-skills.json"

# Managed-entry kinds recorded in the manifest. ``symlink`` and ``junction`` are
# live links to the source; ``copy`` is a one-time snapshot (Windows last resort).
_KIND_SYMLINK = "symlink"
_KIND_JUNCTION = "junction"
_KIND_COPY = "copy"


def _on_windows() -> bool:
    """Platform check, indirected so tests can drive the Windows link path on
    POSIX without globally flipping ``os.name`` (which would also switch
    ``pathlib`` into un-instantiable ``WindowsPath`` mode)."""
    return os.name == "nt"


class SkillSourceMissingError(Exception):
    """Raised when a configured skill source path does not exist."""


@dataclass(frozen=True)
class _Plan:
    skills_root: str
    manifest_path: str


def _claude_plan(cwd: str) -> _Plan:
    return _Plan(
        skills_root=os.path.join(cwd, CLAUDE_SKILLS_SUBDIR),
        manifest_path=os.path.join(cwd, CLAUDE_MANIFEST),
    )


def _agents_plan(cwd: str) -> _Plan:
    return _Plan(
        skills_root=os.path.join(cwd, AGENTS_SKILLS_SUBDIR),
        manifest_path=os.path.join(cwd, AGENTS_MANIFEST),
    )


def prepare_claude_skills(cwd: str, skills: list[str] | tuple[str, ...]) -> str:
    """Materialize skills for Claude Agent SDK auto-discovery.

    Returns the absolute path to ``cwd/.claude/skills`` (always created).
    """
    return _materialize(_claude_plan(cwd), list(skills))


def prepare_deepagents_skills(cwd: str, skills: list[str] | tuple[str, ...]) -> str:
    """Materialize skills for DeepAgents `skills=[...]` parameter.

    Returns the absolute path to ``cwd/.agents/skills`` (the root passed to
    `create_deep_agent(skills=[...])`).
    """
    return _materialize(_agents_plan(cwd), list(skills))


def prepare_codex_skills(cwd: str, skills: list[str] | tuple[str, ...]) -> str:
    """Materialize skills for Codex auto-discovery.

    Codex's first-layer discovery path is ``$CWD/.agents/skills`` (Open Agent
    Skills standard) — same on-disk format as DeepAgents, so we reuse the
    same subtree and manifest. Returns the absolute root.
    """
    return _materialize(_agents_plan(cwd), list(skills))


def _materialize(plan: _Plan, skills: list[str]) -> str:
    os.makedirs(plan.skills_root, exist_ok=True)

    previous = _read_manifest(plan.manifest_path)
    _cleanup_previous(plan.skills_root, previous)

    new_entries: list[tuple[str, str]] = []
    created: dict[str, str] = {}  # name -> kind, for same-call duplicate basenames
    for src in skills:
        if not os.path.isdir(src):
            raise SkillSourceMissingError(f"Skill source path not found or not a directory: {src}")
        name = os.path.basename(os.path.normpath(src))
        if not name:
            raise SkillSourceMissingError(f"Skill source path has no basename: {src}")
        dst = os.path.join(plan.skills_root, name)
        # Clear a same-named entry created earlier in THIS call (duplicate
        # basenames). ``created.get(name)`` lets us remove a prior copy too;
        # a leftover real dir we never wrote is left to raise FileExistsError.
        _remove_managed_entry(dst, created.get(name))
        kind = _create_managed_entry(os.path.abspath(src), dst)
        created[name] = kind
        new_entries.append((name, kind))

    _write_manifest(plan.manifest_path, new_entries)
    return plan.skills_root


def _create_managed_entry(src_abs: str, dst: str) -> str:
    """Create a managed entry at ``dst`` pointing at ``src_abs``; return its kind.

    POSIX uses a symlink. Windows prefers a directory junction (no Developer
    Mode / admin needed, and live edits are reflected like a symlink) and falls
    back to a copy only when junctions are unsupported (FAT/exFAT cwd, network
    share, or a UNC source).
    """
    if _on_windows():
        try:
            import _winapi  # Windows-only; imported lazily so POSIX never touches it.

            # CreateJunction(target, junction_path): the junction at ``dst``
            # points to ``src_abs``.
            _winapi.CreateJunction(src_abs, dst)
            return _KIND_JUNCTION
        except (OSError, AttributeError, ImportError, NotImplementedError) as exc:
            logger.warning(
                "Junction unsupported for %s -> %s (%s); copying instead "
                "(live edits to the source will NOT be reflected).",
                dst,
                src_abs,
                exc,
            )
            shutil.copytree(src_abs, dst)
            return _KIND_COPY

    # ``target_is_directory`` is a Windows-only hint that POSIX ignores — safe
    # to pass everywhere. An absolute target keeps the link valid even if the
    # subprocess later chdirs.
    os.symlink(src_abs, dst, target_is_directory=True)
    return _KIND_SYMLINK


def _read_manifest(path: str) -> list[tuple[str, str]]:
    """Read the manifest as ``(name, kind)`` pairs.

    Backward-compatible: a legacy manifest stored ``managed`` as a list of bare
    name strings (all symlinks at the time), so a string item is read as a
    symlink entry.
    """
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        logger.warning("Could not read skills manifest at %s; treating as empty.", path)
        return []
    raw = data.get("managed", [])
    if not isinstance(raw, list):
        return []
    out: list[tuple[str, str]] = []
    for item in raw:
        if isinstance(item, str):  # legacy format: bare name, always a symlink
            out.append((item, _KIND_SYMLINK))
        elif isinstance(item, dict):
            name = item.get("name")
            kind = item.get("kind")
            if isinstance(name, str) and isinstance(kind, str):
                out.append((name, kind))
    return out


def _write_manifest(path: str, managed: list[tuple[str, str]]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {"managed": [{"name": name, "kind": kind} for name, kind in managed]}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def _cleanup_previous(skills_root: str, previous: list[tuple[str, str]]) -> None:
    for name, kind in previous:
        _remove_managed_entry(os.path.join(skills_root, name), kind)


def _remove_managed_entry(path: str, kind: str | None = None) -> None:
    """Remove a previously-managed entry at ``path`` if present.

    - symlinks (valid or broken) -> ``os.unlink``
    - directory junctions (valid or broken) -> ``os.rmdir`` (removes the reparse
      point only, never the target's contents)
    - a copied directory (``kind == "copy"``) -> ``shutil.rmtree``

    Anything else — a real directory we did not record as a copy, a user file —
    is left alone. The dev-stage policy is to never destroy what we did not
    write; if such an entry sits at ``path``, the subsequent create in
    ``_materialize`` raises ``FileExistsError`` — loud and visible, easier to
    debug than a silent partial state.
    """
    if os.path.islink(path):
        _remove_link(path)
    elif os.path.isjunction(path):
        os.rmdir(path)  # removes the reparse point only, never the target's contents
    elif kind == _KIND_COPY and os.path.isdir(path):
        shutil.rmtree(path)


def _remove_link(path: str) -> None:
    """Remove a symlink cross-platform. ``os.unlink`` covers POSIX links and
    Windows *file* symlinks; a Windows *directory* symlink (incl. a broken one)
    raises ``PermissionError`` from ``unlink`` and needs ``os.rmdir`` instead.
    New Windows entries are junctions, so this only matters for legacy manifests
    written under Developer Mode."""
    try:
        os.unlink(path)
    except OSError:
        if _on_windows():
            os.rmdir(path)
        else:
            raise
