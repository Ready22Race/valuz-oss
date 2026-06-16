"""InjectionAssembler — frozen memory snapshot for a session (memory-system-design §8).

Captures the in-scope memory (USER + global MEMORY + this project's MEMORY) ONCE
per session and reuses the same bytes for the session's life, so the block is
byte-stable (prefix-cache friendly) and never reflects mid-session writes —
those land on disk and surface in the next session. Rides the per-turn
additional-context (host side, ``context_builder``), so it never pollutes the
user-visible ``instructions_md``. Load-time sanitization happens inside
``MemoryStore.render_for_injection``.
"""

from __future__ import annotations

from valuz_agent.modules.memory.service import MemoryStore, memory_store


class InjectionAssembler:
    def __init__(self, store: MemoryStore | None = None) -> None:
        self._store = store or memory_store
        self._snapshots: dict[str, str] = {}

    def snapshot_for_session(self, *, session_id: str, project_id: str | None = None) -> str:
        """Frozen memory block for a session — built once, then reused verbatim."""
        cached = self._snapshots.get(session_id)
        if cached is not None:
            return cached
        block = self._store.render_for_injection(project_id=project_id)
        self._snapshots[session_id] = block
        return block

    def invalidate(self, session_id: str) -> None:
        """Drop a session's cached snapshot (e.g. on session end)."""
        self._snapshots.pop(session_id, None)


injection_assembler = InjectionAssembler()
