"""``_locate_skill_roots`` — enumerate every skill under an import source.

The fix for "a collection/plugin silently imported just one of its skills":
when a directory holds multiple ``SKILL.md`` (e.g. a Claude plugin laid out as
``skills/<name>/SKILL.md``), the importer must surface ALL of them, pruning each
skill's own subtree so a skill's internal folders aren't mistaken for sub-skills.
"""

from __future__ import annotations

from pathlib import Path

from valuz_agent.modules.skills.service import SkillLibraryService


def _svc() -> SkillLibraryService:
    return SkillLibraryService.__new__(SkillLibraryService)  # github/locate helpers need no deps


def _skill(d: Path, name: str) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {name} desc\n---\n", "utf-8")


def test_single_skill_at_root_returns_itself(tmp_path: Path) -> None:
    _skill(tmp_path / "solo", "solo")
    roots = _svc()._locate_skill_roots(tmp_path / "solo")
    assert [p.name for p in roots] == ["solo"]


def test_plugin_collection_yields_every_skill_and_prunes(tmp_path: Path) -> None:
    plug = tmp_path / "markdown-html"
    (plug / ".claude-plugin").mkdir(parents=True)
    (plug / "agents").mkdir()
    (plug / "commands").mkdir()
    (plug / "CLAUDE.md").write_text("plugin", "utf-8")
    for name in ("md-document", "md-review", "md-slides", "orchestrator"):
        _skill(plug / "skills" / name, name)
        # each skill carries its OWN internal files/folders — NOT sub-skills
        (plug / "skills" / name / "scripts").mkdir()
        (plug / "skills" / name / "scripts" / "x.py").write_text("x", "utf-8")
    # Adversarial: a SKILL.md nested INSIDE a skill must NOT be double-counted
    # (pruning). If pruning were missing this would surface a phantom 5th skill.
    nested = plug / "skills" / "md-document" / "examples" / "demo"
    _skill(nested, "should-be-pruned")

    roots = _svc()._locate_skill_roots(plug)
    names = sorted(p.name for p in roots)
    assert names == ["md-document", "md-review", "md-slides", "orchestrator"]
    assert "demo" not in names  # nested SKILL.md pruned, not surfaced


def test_no_skill_returns_empty(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "README.md").write_text("hi", "utf-8")
    assert _svc()._locate_skill_roots(tmp_path) == []
