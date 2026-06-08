"""``_download_repo_zipball`` — branch resolution for GitHub imports.

A bare repo URL (``github.com/owner/repo``) carries no ref. Resolving the
default branch through the GitHub REST API rate-limits hard, so the importer
instead tries the common defaults (``main`` then ``master``) directly against
**codeload** (no rate limit) and only falls back to the API for the rare repo
whose default is neither. These tests pin that behaviour with a stubbed
downloader — no network.
"""

from __future__ import annotations

import urllib.error
from pathlib import Path

from valuz_agent.modules.skills.service import SkillLibraryService


def _svc() -> SkillLibraryService:
    return SkillLibraryService.__new__(SkillLibraryService)


def _http_404(branch: str) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        f"https://codeload.github.com/.../{branch}", 404, "Not Found", {}, None  # type: ignore[arg-type]
    )


def test_bare_repo_uses_main_without_api(tmp_path: Path) -> None:
    svc = _svc()
    seen: list[str] = []

    def fake_download(url: str, target: Path) -> None:
        seen.append(url)

    def boom_api(owner: str, repo: str) -> str:  # must NOT be called
        raise AssertionError("default-branch API must not be hit for a main repo")

    svc._download_file = fake_download  # type: ignore[method-assign]
    svc._github_default_branch = boom_api  # type: ignore[method-assign]

    used = svc._download_repo_zipball("o", "r", None, tmp_path / "x.zip")
    assert used == "main"
    assert seen == ["https://codeload.github.com/o/r/zip/refs/heads/main"]


def test_bare_repo_falls_back_to_master(tmp_path: Path) -> None:
    svc = _svc()
    calls: list[str] = []

    def fake_download(url: str, target: Path) -> None:
        branch = url.rsplit("/", 1)[-1]
        calls.append(branch)
        if branch == "main":
            raise _http_404("main")
        # master succeeds

    svc._download_file = fake_download  # type: ignore[method-assign]
    svc._github_default_branch = lambda o, r: (_ for _ in ()).throw(  # type: ignore[method-assign]
        AssertionError("API must not be hit when master exists")
    )

    used = svc._download_repo_zipball("o", "r", None, tmp_path / "x.zip")
    assert used == "master"
    assert calls == ["main", "master"]


def test_bare_repo_neither_default_consults_api(tmp_path: Path) -> None:
    svc = _svc()
    downloaded: list[str] = []

    def fake_download(url: str, target: Path) -> None:
        branch = url.rsplit("/", 1)[-1]
        if branch in ("main", "master"):
            raise _http_404(branch)
        downloaded.append(branch)  # the API-resolved branch succeeds

    svc._download_file = fake_download  # type: ignore[method-assign]
    svc._github_default_branch = lambda o, r: "trunk"  # type: ignore[method-assign]

    used = svc._download_repo_zipball("o", "r", None, tmp_path / "x.zip")
    assert used == "trunk"
    assert downloaded == ["trunk"]


def test_known_branch_downloads_directly(tmp_path: Path) -> None:
    svc = _svc()
    seen: list[str] = []

    svc._download_file = lambda url, target: seen.append(url)  # type: ignore[method-assign]
    svc._github_default_branch = lambda o, r: (_ for _ in ()).throw(  # type: ignore[method-assign]
        AssertionError("API must not be hit when the branch is explicit")
    )

    used = svc._download_repo_zipball("o", "r", "dev", tmp_path / "x.zip")
    assert used == "dev"
    assert seen == ["https://codeload.github.com/o/r/zip/refs/heads/dev"]
