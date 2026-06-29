"""Pure packaging tests for the ``.valuzpack`` writer/reader.

These exercise the cross-OS path handling that broke real imports: a skill whose
slug was stored as a full path — Windows ``C:/Users/...`` (with ``/`` or ``\\``
separators) or POSIX ``/home/...``. The old writer embedded that verbatim, so the
archive carried entries like ``skills/C:/Users/.../SKILL.md`` that the Windows
importer rejected ("unsafe path in archive") and the POSIX importer silently
mis-nested. We assert the writer now sanitizes, and the reader both sanitizes new
packs and rescues legacy ones — without weakening the zip-slip guard.

Every OS is simulated through the archive *strings*: zip entry names and manifest
slugs are plain text, identical regardless of which host produced them, so one
deterministic run on any machine covers Windows / macOS / Linux logic.
"""

from __future__ import annotations

import io
import json
import shutil
import zipfile
from pathlib import Path

import pytest

from valuz_agent.modules.agent_packs.manifest import (
    AgentPackManifest,
    PackAgent,
    PackCollection,
    PackSkill,
)
from valuz_agent.modules.agent_packs.packaging import (
    MANIFEST_NAME,
    SKILLS_DIR,
    PackArchiveError,
    build_archive,
    embedded_skill_dir,
    extract_archive,
    sanitize_skill_slug,
)

# --- helpers ---------------------------------------------------------------

# The same skill rendered three ways — a clean slug and the path-shaped slugs
# each OS produced when the bug stored a full path as the "slug".
WIN_FWD = "C:/Users/Think/.agents/skills/price-audit"
WIN_BACK = "C:\\Users\\Think\\.agents\\skills\\price-audit"
POSIX_ABS = "/home/think/.agents/skills/price-audit"


def _manifest(agent_skills: list[str], pack_skills: list[str]) -> AgentPackManifest:
    return AgentPackManifest(
        collection=PackCollection(name="Pack"),
        agents=[PackAgent(slug="analyst", name="Analyst", skills=list(agent_skills))],
        skills=[PackSkill(slug=s, source="embedded") for s in pack_skills],
    )


def _raw_pack(manifest: dict | bytes, files: dict[str, bytes]) -> bytes:
    """Hand-build a zip (used to simulate legacy / hostile archives the real
    writer would never emit)."""
    body = (
        manifest
        if isinstance(manifest, (bytes, bytearray))
        else json.dumps(manifest).encode("utf-8")
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(MANIFEST_NAME, body)
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _legacy_manifest_dict(slug: str) -> dict:
    """An OLD-style manifest whose embedded skill + agent reference both use a
    raw (path-shaped) slug, exactly as the buggy exporter wrote it."""
    return {
        "schema_version": 1,
        "kind": "agent-pack",
        "collection": {"name": "Legacy"},
        "agents": [{"slug": "analyst", "name": "Analyst", "skills": [slug]}],
        "skills": [{"slug": slug, "source": "embedded"}],
        "connectors": [],
    }


def _entry_names(data: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return [i.filename for i in zf.infolist() if not i.is_dir()]


def _skill_src(tmp_path: Path) -> Path:
    src = tmp_path / "skill"
    (src / "scripts").mkdir(parents=True)
    (src / "SKILL.md").write_text("audit body", encoding="utf-8")
    (src / "scripts" / "run.py").write_text("print(1)", encoding="utf-8")
    return src


# --- sanitize_skill_slug ---------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("price-audit", "price-audit"),  # already clean (macOS/Linux normal)
        (WIN_FWD, "price-audit"),  # windows, forward slashes
        (WIN_BACK, "price-audit"),  # windows, backslash separators
        (POSIX_ABS, "price-audit"),  # posix absolute path
        ("./skills/price-audit", "price-audit"),  # relative
        ("price-audit/", "price-audit"),  # trailing slash
        ("price.audit_v2", "price.audit_v2"),  # dots / underscores preserved
    ],
)
def test_sanitize_extracts_trailing_segment(raw: str, expected: str) -> None:
    assert sanitize_skill_slug(raw) == expected


@pytest.mark.parametrize("degenerate", ["", "..", ".", "C:", "/", "\\", "//"])
def test_sanitize_degenerate_is_one_safe_segment(degenerate: str) -> None:
    out = sanitize_skill_slug(degenerate)
    assert out
    assert "/" not in out and "\\" not in out and ":" not in out
    assert out not in ("..", ".")


# --- build_archive: the writer sanitizes -----------------------------------


@pytest.mark.parametrize(
    "slug_key", ["price-audit", WIN_FWD, WIN_BACK, POSIX_ABS]
)
def test_build_archive_emits_clean_skill_entries(tmp_path: Path, slug_key: str) -> None:
    data = build_archive(
        _manifest(["price-audit"], ["price-audit"]), {slug_key: _skill_src(tmp_path)}
    )
    names = _entry_names(data)
    skill_entries = {n for n in names if n.startswith(f"{SKILLS_DIR}/")}
    assert skill_entries == {
        "skills/price-audit/SKILL.md",
        "skills/price-audit/scripts/run.py",
    }
    # No drive letter, backslash, or absolute/double-slash leaked into any entry.
    for n in names:
        assert ":" not in n and "\\" not in n and "//" not in n


# --- extract_archive: clean roundtrip --------------------------------------


def test_roundtrip_clean(tmp_path: Path) -> None:
    data = build_archive(
        _manifest(["price-audit"], ["price-audit"]), {"price-audit": _skill_src(tmp_path)}
    )
    manifest, root = extract_archive(data)
    try:
        d = embedded_skill_dir(root, "price-audit")
        assert d is not None
        assert (d / "SKILL.md").read_text(encoding="utf-8") == "audit body"
        assert (d / "scripts" / "run.py").read_text(encoding="utf-8") == "print(1)"
        assert manifest.skills[0].slug == "price-audit"
        assert manifest.agents[0].skills == ["price-audit"]
    finally:
        shutil.rmtree(root, ignore_errors=True)


# --- extract_archive: legacy packs, one per OS -----------------------------


@pytest.mark.parametrize(
    "slug, entry",
    [
        # Windows export, forward slashes — the exact reported failure.
        (WIN_FWD, f"{SKILLS_DIR}/{WIN_FWD}/SKILL.md"),
        # Windows export, backslash separators.
        (WIN_BACK, f"{SKILLS_DIR}/{WIN_BACK}/SKILL.md"),
        # POSIX export, absolute slug → the old f-string made a double slash.
        (POSIX_ABS, f"{SKILLS_DIR}/{POSIX_ABS}/SKILL.md"),
    ],
    ids=["windows-fwd", "windows-back", "posix-abs"],
)
def test_extract_rescues_legacy_pack(slug: str, entry: str) -> None:
    data = _raw_pack(_legacy_manifest_dict(slug), {entry: b"legacy body"})
    manifest, root = extract_archive(data)
    try:
        # Slug normalized everywhere (index + agent reference).
        assert manifest.skills[0].slug == "price-audit"
        assert manifest.agents[0].skills == ["price-audit"]
        # Skill file rescued under the clean slug dir.
        d = embedded_skill_dir(root, "price-audit")
        assert d is not None
        assert (d / "SKILL.md").read_text(encoding="utf-8") == "legacy body"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_extract_legacy_multifile_skill_preserves_tree() -> None:
    """A nested file under a path-shaped slug keeps its sub-path after rescue."""
    data = _raw_pack(
        _legacy_manifest_dict(WIN_FWD),
        {
            f"{SKILLS_DIR}/{WIN_FWD}/SKILL.md": b"top",
            f"{SKILLS_DIR}/{WIN_FWD}/scripts/run.py": b"nested",
        },
    )
    _, root = extract_archive(data)
    try:
        d = embedded_skill_dir(root, "price-audit")
        assert d is not None
        assert (d / "SKILL.md").read_bytes() == b"top"
        assert (d / "scripts" / "run.py").read_bytes() == b"nested"
    finally:
        shutil.rmtree(root, ignore_errors=True)


# --- extract_archive: zip-slip guard stays intact --------------------------


def test_rejects_traversal_outside_skills() -> None:
    data = _raw_pack(
        {
            "schema_version": 1,
            "kind": "agent-pack",
            "collection": {"name": "Evil"},
            "agents": [{"slug": "a", "name": "A"}],
            "skills": [],
            "connectors": [],
        },
        {"../../../../etc/passwd": b"pwned"},
    )
    with pytest.raises(PackArchiveError, match="unsafe path"):
        extract_archive(data)


def test_rejects_traversal_in_skill_tail() -> None:
    data = _raw_pack(
        _legacy_manifest_dict("price-audit"),
        {f"{SKILLS_DIR}/price-audit/../../../../etc/passwd": b"pwned"},
    )
    with pytest.raises(PackArchiveError, match="unsafe path"):
        extract_archive(data)


def test_hostile_pathy_slug_is_contained_not_escaped() -> None:
    # A malicious manifest declares an embedded slug that is itself a traversal.
    # It must collapse to a contained segment, never escape the extract root.
    slug = "../../evil"
    data = _raw_pack(
        _legacy_manifest_dict(slug),
        {f"{SKILLS_DIR}/{slug}/SKILL.md": b"x"},  # entry: skills/../../evil/SKILL.md
    )
    manifest, root = extract_archive(data)
    try:
        assert manifest.skills[0].slug == "evil"
        d = embedded_skill_dir(root, "evil")
        assert d is not None
        # Everything stayed under the temp root.
        assert root.resolve() in (d.resolve(), *d.resolve().parents)
    finally:
        shutil.rmtree(root, ignore_errors=True)


# --- extract_archive: caps + malformed -------------------------------------


def test_rejects_bad_zip() -> None:
    with pytest.raises(PackArchiveError, match="bad zip"):
        extract_archive(b"this is not a zip")


def test_rejects_missing_manifest() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{SKILLS_DIR}/x/SKILL.md", b"x")
    with pytest.raises(PackArchiveError, match="missing manifest"):
        extract_archive(buf.getvalue())


def test_rejects_invalid_manifest() -> None:
    data = _raw_pack(b"{ not valid json", {})
    with pytest.raises(PackArchiveError, match="invalid manifest"):
        extract_archive(data)


def test_rejects_oversized_file() -> None:
    big = b"\0" * (5 * 1024 * 1024 + 1)  # one byte over the 5 MiB per-file cap
    data = _raw_pack(_legacy_manifest_dict("price-audit"), {"skills/price-audit/big.bin": big})
    with pytest.raises(PackArchiveError, match="per-file size limit"):
        extract_archive(data)


def test_rejects_too_many_files() -> None:
    files = {f"skills/price-audit/f{i}.txt": b"x" for i in range(2048)}  # +manifest > 2048
    data = _raw_pack(_legacy_manifest_dict("price-audit"), files)
    with pytest.raises(PackArchiveError, match="file limit"):
        extract_archive(data)
