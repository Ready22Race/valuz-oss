"""Primitives tests for ``infra/git_worktree`` against real temp repos."""

import subprocess
from pathlib import Path

import pytest

from valuz_agent.infra import git_worktree as gw

pytestmark = pytest.mark.skipif(
    not gw.git_available(), reason="git not available on this machine"
)


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


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


# ---- slug rules ------------------------------------------------------------


def test_validate_slug_accepts_reasonable_names():
    for slug in ("fix-login", "user/feature", "a.b_c-1"):
        gw.validate_slug(slug)


@pytest.mark.parametrize(
    "bad",
    ["", "a" * 65, "../x", "a/../b", "a//b", "/abs", "trail/", "spa ce", "拼音"],
)
def test_validate_slug_rejects(bad: str):
    with pytest.raises(gw.InvalidWorktreeSlugError):
        gw.validate_slug(bad)


def test_flatten_and_branch_are_injective():
    assert gw.flatten_slug("user/feature") == "user+feature"
    assert gw.branch_name("u", "user/feature") == "valuz/u-user+feature"
    # '+' is not in the slug allowlist, so no other slug collides.
    with pytest.raises(gw.InvalidWorktreeSlugError):
        gw.validate_slug("user+feature")


# ---- detect ----------------------------------------------------------------


def test_detect_git(repo: Path, tmp_path: Path):
    info = gw.detect_git(repo)
    assert info is not None
    assert info.git_root == repo.resolve()
    assert info.common_dir == (repo / ".git").resolve()

    sub = repo / "pkg" / "web"
    sub.mkdir(parents=True)
    sub_info = gw.detect_git(sub)
    assert sub_info is not None and sub_info.git_root == repo.resolve()

    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    assert gw.detect_git(plain) is None


# ---- create / resume / changes / remove ------------------------------------


def test_get_or_create_and_fast_resume(repo: Path):
    wt = gw.get_or_create(repo, "feat-x", "u")
    assert wt.created is True
    assert wt.path == repo / ".valuz" / "worktrees" / "feat-x"
    assert (wt.path / "README.md").exists()
    assert wt.branch == "valuz/u-feat-x"
    assert wt.head_sha == _git(repo, "rev-parse", "HEAD")

    again = gw.get_or_create(repo, "feat-x", "u")
    assert again.created is False
    assert again.path == wt.path
    assert again.head_sha == wt.head_sha


def test_has_changes_fail_closed(repo: Path):
    wt = gw.get_or_create(repo, "feat-y", "u")
    assert gw.has_changes(wt.path, wt.head_sha) is False

    # Dirty working tree counts.
    (wt.path / "new.txt").write_text("x")
    assert gw.has_changes(wt.path, wt.head_sha) is True

    # Committed work counts even with a clean tree.
    _git(wt.path, "add", ".")
    _git(wt.path, "commit", "-m", "wip")
    assert gw.has_changes(wt.path, wt.head_sha) is True
    status = gw.status_counts(wt.path, wt.head_sha)
    assert status is not None
    assert status.dirty_files == 0 and status.ahead_commits == 1

    # Unverifiable state fails closed.
    assert gw.has_changes(repo / "no-such-dir", wt.head_sha) is True


def test_remove_deletes_worktree_and_branch(repo: Path):
    wt = gw.get_or_create(repo, "feat-z", "u")
    gw.remove(repo, wt.path, wt.branch)
    assert not wt.path.exists()
    branches = _git(repo, "branch", "--list", wt.branch)
    assert branches == ""


def test_list_worktrees_only_managed(repo: Path):
    gw.get_or_create(repo, "one", "u")
    gw.get_or_create(repo, "two", "task")
    # A foreign worktree outside .valuz/worktrees must be ignored.
    foreign = repo.parent / "foreign-wt"
    _git(repo, "worktree", "add", "-b", "foreign", str(foreign))

    listed = gw.list_worktrees(repo)
    names = sorted(w.path.name for w in listed)
    assert names == ["one", "two"]
    branches = {w.path.name: w.branch for w in listed}
    assert branches == {"one": "valuz/u-one", "two": "valuz/task-two"}


def test_ensure_info_exclude_idempotent(repo: Path):
    common = (repo / ".git").resolve()
    gw.ensure_info_exclude(common)
    gw.ensure_info_exclude(common)
    content = (common / "info" / "exclude").read_text()
    assert content.count(".valuz/\n") == 1
    # And it actually hides the worktrees dir from status.
    gw.get_or_create(repo, "hidden", "u")
    assert ".valuz" not in _git(repo, "status", "--porcelain")
