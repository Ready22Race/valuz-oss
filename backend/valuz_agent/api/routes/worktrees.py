"""Project worktree endpoints — computed on read, git is the source of truth.

Contract: ``/v1/projects/{project_id}/worktrees`` in ``api/openapi.yaml``.
The list response carries the project's git facts (``ProjectGitInfo``) so the
frontend can gate the "run in worktree" toggle with the same single fetch.
"""

from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException

from valuz_agent.api.deps import get_current_user_id, get_project_service
from valuz_agent.modules.projects.service import ProjectDetail, ProjectService
from valuz_agent.modules.worktrees.service import (
    ProjectGitInfo,
    WorktreeItem,
    worktree_service,
)

router = APIRouter(prefix="/v1/projects", tags=["projects"])


@dataclass
class WorktreeListResponse:
    git: ProjectGitInfo
    worktrees: list[WorktreeItem]


async def _project_or_404(
    svc: ProjectService, user_id: str, project_id: str
) -> ProjectDetail:
    # WorktreeService duck-types the row (id / kind / root_path), which
    # ProjectDetail satisfies.
    try:
        return await svc.get_project(user_id, project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown project: {project_id}") from exc


@router.get("/{project_id}/worktrees")
async def list_project_worktrees(
    project_id: str,
    user_id: str = Depends(get_current_user_id),
    projects: ProjectService = Depends(get_project_service),
) -> WorktreeListResponse:
    project = await _project_or_404(projects, user_id, project_id)
    git = await worktree_service.project_git(user_id, project)
    worktrees = (
        await worktree_service.list_for_project(user_id, project) if git.is_repo else []
    )
    return WorktreeListResponse(git=git, worktrees=worktrees)


@router.delete("/{project_id}/worktrees/{name}", status_code=204)
async def discard_project_worktree(
    project_id: str,
    name: str,
    force: bool = False,
    user_id: str = Depends(get_current_user_id),
    projects: ProjectService = Depends(get_project_service),
) -> None:
    project = await _project_or_404(projects, user_id, project_id)
    # WorktreeDirty (409) / WorktreeNotFound (404) map via the ValuzError
    # middleware; the 409 detail carries the dirty/ahead counts for the
    # frontend's confirm dialog.
    await worktree_service.discard(user_id, project, name, force=force)
