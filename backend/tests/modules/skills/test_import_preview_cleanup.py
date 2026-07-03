"""Durable import-preview cleanup."""

from __future__ import annotations

from pathlib import Path

from valuz_agent.modules.skills.service import SkillLibraryService


def _svc() -> SkillLibraryService:
    return SkillLibraryService.__new__(SkillLibraryService)


def test_persisted_archive_preview_cleanup_removes_content_root(tmp_path, monkeypatch) -> None:
    from valuz_agent.infra import fs_registry as fsr

    monkeypatch.setattr(fsr.settings, "user_temp_dir", tmp_path / "temp" / "{user_id}")
    user_id = "u"
    content_root = tmp_path / "archive-content"
    skill = content_root / "extract" / "my-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("x", "utf-8")

    svc = _svc()
    svc._write_import_preview_record(
        user_id,
        "arch",
        kind="archive",
        skill_root=skill,
        managed_temp=True,
        cleanup_root=content_root,
    )

    svc._cleanup_preview("arch", user_id=user_id)
    assert not content_root.exists()


def test_persisted_url_preview_cleanup_keeps_siblings_until_last(
    tmp_path: Path, monkeypatch
) -> None:
    from valuz_agent.infra import fs_registry as fsr

    monkeypatch.setattr(fsr.settings, "user_temp_dir", tmp_path / "temp" / "{user_id}")
    user_id = "u"
    staging = tmp_path / "valuz-skill-url-x"
    skill_a = staging / "skills" / "a"
    skill_b = staging / "skills" / "b"
    skill_a.mkdir(parents=True)
    skill_b.mkdir(parents=True)
    (skill_a / "SKILL.md").write_text("---\nname: a\n---\n", "utf-8")
    (skill_b / "SKILL.md").write_text("---\nname: b\n---\n", "utf-8")

    svc = _svc()
    svc._write_import_preview_record(
        user_id,
        "pid-a",
        kind="url",
        skill_root=skill_a,
        cleanup_root=staging,
        created_at=0.0,
    )
    svc._write_import_preview_record(
        user_id,
        "pid-b",
        kind="url",
        skill_root=skill_b,
        cleanup_root=staging,
        created_at=0.0,
    )

    svc._cleanup_preview("pid-a", user_id=user_id)
    assert skill_b.exists()
    assert staging.exists()

    svc._cleanup_preview("pid-b", user_id=user_id)
    assert not staging.exists()
