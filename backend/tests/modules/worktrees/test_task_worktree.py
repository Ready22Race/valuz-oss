"""Task-level worktree support tests (modules/tasks/task_worktree)."""

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from valuz_agent.infra import git_worktree as gw
from valuz_agent.modules.tasks.task_worktree import (
    cleanup_task_worktree_if_clean,
    resolve_task_cwd,
    task_worktree_notice,
    task_worktree_snapshot,
)
from valuz_agent.modules.worktrees.service import WorktreeService

pytestmark = pytest.mark.skipif(
    not gw.git_available(), reason="git not available on this machine"
)

USER = "local-test-owner"


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


@dataclass
class FakeProjectRow:
    id: str
    kind: str
    root_path: str | None


@dataclass
class FakeTaskRow:
    id: str = "task-1"
    metadata_: dict[str, Any] = field(default_factory=dict)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "T")
    (root / "README.md").write_text("hello\n")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "init")
    return root


async def _task_with_worktree(repo: Path, name: str = "task-abc") -> FakeTaskRow:
    svc = WorktreeService()
    project = FakeProjectRow(id="p1", kind="project", root_path=str(repo))
    handle = await svc.get_or_create(USER, project, name=name, origin="task")
    return FakeTaskRow(
        metadata_={
            "worktree": {
                "name": handle.name,
                "branch": handle.branch,
                "path": handle.path,
                "git_root": handle.git_root,
                "base_sha": handle.base_sha,
                "cwd": handle.session_cwd,
            }
        }
    )


async def test_resolve_cwd_without_snapshot_returns_default():
    row = FakeTaskRow()
    assert task_worktree_snapshot(row) is None
    assert await resolve_task_cwd(row, "/main/cwd") == "/main/cwd"


async def test_resolve_cwd_uses_snapshot(repo: Path):
    row = await _task_with_worktree(repo)
    snapshot = task_worktree_snapshot(row)
    assert snapshot is not None
    assert await resolve_task_cwd(row, str(repo)) == snapshot["cwd"]


async def test_resolve_cwd_heals_removed_worktree(repo: Path):
    row = await _task_with_worktree(repo, name="task-heal")
    snapshot = task_worktree_snapshot(row)
    assert snapshot is not None
    path = Path(str(snapshot["path"]))
    # Simulate someone discarding the worktree mid-task.
    _git(repo, "worktree", "remove", "--force", str(path))
    assert not path.exists()

    cwd = await resolve_task_cwd(row, str(repo))
    assert cwd == snapshot["cwd"]
    assert (path / ".git").exists()  # recreated at the deterministic path


async def test_notice_mentions_branch_and_main_workspace(repo: Path):
    row = await _task_with_worktree(repo, name="task-notice")
    snapshot = task_worktree_snapshot(row)
    notice = task_worktree_notice(snapshot)
    assert notice is not None
    assert "valuz/task-task-notice" in notice
    assert str(repo.resolve()) in notice
    assert task_worktree_notice(None) is None


async def test_cleanup_removes_clean_keeps_dirty(repo: Path):
    row = await _task_with_worktree(repo, name="task-clean")
    snapshot = task_worktree_snapshot(row)
    assert snapshot is not None
    assert await cleanup_task_worktree_if_clean(row) is True
    assert not Path(str(snapshot["path"])).exists()

    dirty_row = await _task_with_worktree(repo, name="task-dirty")
    dirty_snapshot = task_worktree_snapshot(dirty_row)
    assert dirty_snapshot is not None
    (Path(str(dirty_snapshot["path"])) / "wip.txt").write_text("keep me")
    assert await cleanup_task_worktree_if_clean(dirty_row) is False
    assert Path(str(dirty_snapshot["path"])).exists()
