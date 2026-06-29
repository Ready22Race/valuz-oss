"""Packaging tests for ``build_project_archive`` / ``extract_project_archive``.

Mirrors the agent-packaging guarantees: round-trip, zip-slip rejection,
oversized rejection, missing manifest rejection. Adds memory-dir coverage
(absent → no memory/ entries; present → bytes-for-bytes restore).
"""

from __future__ import annotations

import io
import zipfile

import pytest

from valuz_agent.modules.project_packs.manifest import (
    ProjectMeta,
    ProjectPackManifest,
)
from valuz_agent.modules.project_packs.packaging import (
    ProjectPackArchiveError,
    build_project_archive,
    extract_project_archive,
    memory_root,
)


def _manifest() -> ProjectPackManifest:
    return ProjectPackManifest(project=ProjectMeta(name="My Project", kind="project"))


def test_roundtrip_with_skill_and_memory(tmp_path) -> None:
    skill = tmp_path / "my-skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# My Skill\n", encoding="utf-8")
    memory = tmp_path / "memory"
    memory.mkdir()
    (memory / "MEMORY.md").write_text("# project memory\n", encoding="utf-8")
    (memory / "sub").mkdir()
    (memory / "sub" / "notes.md").write_text("nested note\n", encoding="utf-8")

    from valuz_agent.modules.agent_packs.manifest import PackSkill

    manifest = _manifest()
    manifest.skills = [PackSkill(slug="my-skill", source="embedded")]
    data = build_project_archive(manifest, {"my-skill": skill}, memory)

    parsed, root = extract_project_archive(data)
    assert parsed.project.name == "My Project"
    # skill files restored
    assert (root / "skills" / "my-skill" / "SKILL.md").read_text() == "# My Skill\n"
    # memory restored byte-for-byte
    mem_root = memory_root(root)
    assert mem_root is not None
    assert (mem_root / "MEMORY.md").read_text() == "# project memory\n"
    assert (mem_root / "sub" / "notes.md").read_text() == "nested note\n"


def test_roundtrip_no_memory_dir() -> None:
    """When memory_dir=None the archive carries no memory/ entries."""
    data = build_project_archive(_manifest(), {}, None)
    parsed, root = extract_project_archive(data)
    assert parsed.project.name == "My Project"
    assert not (root / "memory").is_dir()


def test_roundtrip_empty_memory_dir_treated_as_absent(tmp_path) -> None:
    """Empty memory dir → archive carries no memory entries (the export
    path passes None when the dir has no files)."""
    # build_project_archive itself doesn't filter; the service does. But
    # passing an empty dir here still produces no entries (rglob files).
    empty = tmp_path / "empty-memory"
    empty.mkdir()
    data = build_project_archive(_manifest(), {}, empty)
    parsed, root = extract_project_archive(data)
    assert parsed.project.name == "My Project"
    # No files under memory/ (dir may or may not exist as a prefix; no
    # extract entries were written for the empty dir).
    mem_root = memory_root(root)
    if mem_root is not None:
        assert not any(mem_root.rglob("*"))


def test_extract_rejects_non_zip() -> None:
    with pytest.raises(ProjectPackArchiveError):
        extract_project_archive(b"not a zip file")


def test_extract_rejects_missing_manifest() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("memory/notes.md", "no manifest here")
    with pytest.raises(ProjectPackArchiveError):
        extract_project_archive(buf.getvalue())


def test_extract_rejects_zip_slip(tmp_path) -> None:
    """A traversal entry (../escape) must be rejected as unsafe."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", _manifest().model_dump_json())
        # Construct a malicious entry that resolves outside the root.
        zf.writestr("memory/../../../../escape.md", "evil")
    with pytest.raises(ProjectPackArchiveError):
        extract_project_archive(buf.getvalue())


def test_extract_rejects_oversized_file() -> None:
    """A file exceeding the per-file size cap must be rejected."""
    payload = b"x" * (6 * 1024 * 1024)  # 6 MiB > 5 MiB cap
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", _manifest().model_dump_json())
        zf.writestr("memory/big.bin", payload)
    with pytest.raises(ProjectPackArchiveError):
        extract_project_archive(buf.getvalue())
