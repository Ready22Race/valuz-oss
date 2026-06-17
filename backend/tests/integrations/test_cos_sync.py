"""User-scoped COS sync — pushes a user's projects + skills to COS under a
``{user_id}/...`` prefix. Mocked store; live COS covered in dev."""

from __future__ import annotations

from pathlib import Path

from valuz_agent.integrations.cos_sync import SyncSource, sync_local_to_cos


class _MemStore:
    def __init__(self) -> None:
        self.objs: dict[str, bytes] = {}

    async def put_bytes(self, key: str, data: bytes) -> None:
        self.objs[key] = data


async def test_sync_uploads_each_source_under_user_prefix(tmp_path):
    proj = tmp_path / "projects"
    (proj / "p1").mkdir(parents=True)
    (proj / "p1" / "main.py").write_bytes(b"x=1")
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "SKILL.md").write_bytes(b"# skill")

    store = _MemStore()
    report = await sync_local_to_cos(
        "user-42",
        store=store,
        sources=[SyncSource("projects", proj), SyncSource("skills/official", skills)],
    )

    # keys live under {user_id}/{source-name}/...
    assert "user-42/projects/p1/main.py" in store.objs
    assert "user-42/skills/official/SKILL.md" in store.objs
    assert report.root_prefix == "user-42"
    assert report.total_files == 2
    assert dict((n, f) for n, f, _ in report.per_source) == {"projects": 1, "skills/official": 1}


async def test_sync_skips_missing_source_dirs(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (real / "f.txt").write_bytes(b"hi")
    store = _MemStore()
    report = await sync_local_to_cos(
        "u",
        store=store,
        sources=[
            SyncSource("real", real),
            SyncSource("ghost", Path(tmp_path / "does-not-exist")),
        ],
    )
    # stage_directory over a missing dir simply yields nothing; no crash.
    assert "u/real/f.txt" in store.objs
    assert report.total_files == 1
