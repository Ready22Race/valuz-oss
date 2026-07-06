from valuz_agent.infra.errors import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    UnprocessableEntityError,
    ValuzError,
)


class InvalidWorktreeName(BadRequestError):
    error_code = 400_741  # HTTP(3) + module(74) + sequence(1)
    message = "Invalid worktree name"


class WorktreeNotAvailable(UnprocessableEntityError):
    error_code = 422_741
    message = "Worktrees require the project to be a git repository and git to be installed"


class WorktreeNotFound(NotFoundError):
    error_code = 404_741
    message = "Worktree not found"


class WorktreeDirty(ConflictError):
    """Discard refused: the worktree holds unmerged work (fail-closed)."""

    error_code = 409_741
    message = "Worktree has uncommitted changes or unmerged commits"


class WorktreeOperationFailed(ValuzError):
    error_code = 500_741
    message = "Git worktree operation failed"
