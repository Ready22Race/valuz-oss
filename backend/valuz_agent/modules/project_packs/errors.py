"""Project Pack module errors.

Mirrors ``modules/agent_packs/errors.py`` but under module code 73 so
``ProjectPack*`` failures carry their own stable ``error_code`` namespace
(HTTP(3) + module(2) + sequence(2))."""

from __future__ import annotations

from valuz_agent.infra.errors import (
    BadRequestError,
    NotFoundError,
    UnprocessableEntityError,
)


class ProjectPackNotFound(NotFoundError):
    error_code = 404_731  # HTTP(3) + module(73) + sequence(01)
    message = "Project pack not found"


class ProjectPackImportFailed(BadRequestError):
    error_code = 400_731  # HTTP(3) + module(73) + sequence(01)
    message = "Project pack import failed"


class ProjectNotExportable(UnprocessableEntityError):
    error_code = 422_731  # HTTP(3) + module(73) + sequence(01)
    message = "Project is not exportable (chat projects stay in place)"
