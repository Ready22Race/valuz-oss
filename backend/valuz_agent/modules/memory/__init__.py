"""Memory module — global + project scoped agent memory.

See ``docs/design/memory-system-design.md`` for the architecture.

P0: three flat ``§``-delimited files (``USER.md`` + ``MEMORY.md`` at the memories
root, ``projects/<id>/MEMORY.md`` per project), a single runtime-agnostic
``memory`` MCP tool (add/replace/remove), and a frozen-snapshot injection. The
service layer is pure (it takes ``project_id`` explicitly and does not couple to
the DB/kernel), so it is fully unit-testable; callers (tool, injection,
extractor) resolve the project id from the kernel session.
"""

from valuz_agent.modules.memory.models import (
    CHAR_LIMITS,
    ENTRY_DELIMITER,
    TARGETS,
    Source,
    Target,
)
from valuz_agent.modules.memory.service import MemoryError, MemoryStore, memory_store

__all__ = [
    "CHAR_LIMITS",
    "ENTRY_DELIMITER",
    "TARGETS",
    "Source",
    "Target",
    "MemoryError",
    "MemoryStore",
    "memory_store",
]
