"""Agent Pack module errors."""

from __future__ import annotations

from valuz_agent.infra.errors import BadRequestError, NotFoundError


class PackNotFound(NotFoundError):
    error_code = 404_721  # HTTP(3) + module(72) + sequence(01)
    message = "Agent pack not found"


class PackImportFailed(BadRequestError):
    error_code = 400_721  # HTTP(3) + module(72) + sequence(01)
    message = "Agent pack import failed"
