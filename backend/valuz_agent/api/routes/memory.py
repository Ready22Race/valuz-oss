"""HTTP surface for memory management (memory-system-design §11 / P2 #6).

Lets the UI inspect what the agent remembers, prune individual entries or clear
a scope, and toggle the memory system on/off. Single-user OSS: the memory files
live under the shared data dir, so these endpoints scope by ``target`` (+ an
optional ``project_id``) rather than per-user; the toggle reads/writes run under
the request's auth context like the other preferences.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.memory import Target, memory_store
from valuz_agent.modules.memory.service import MemoryError
from valuz_agent.modules.settings.preferences import (
    get_memory_auto_extract,
    get_memory_enabled,
    set_memory_auto_extract,
    set_memory_enabled,
)

router = APIRouter(prefix="/v1/memory", tags=["memory"])


class MemoryView(BaseModel):
    enabled: bool
    auto_extract: bool
    # Entries per scope, keyed by target (user / global / project-when-bound).
    entries: dict[str, list[str]]


class MemorySettings(BaseModel):
    enabled: bool
    auto_extract: bool


class MemorySettingsPatch(BaseModel):
    enabled: bool | None = None
    auto_extract: bool | None = None


class MemoryEntryDelete(BaseModel):
    target: Target
    old_text: str
    project_id: str | None = None


class MemoryClear(BaseModel):
    target: Target
    project_id: str | None = None


async def _view(project_id: str | None) -> MemoryView:
    async with async_unit_of_work(commit=False) as db:
        enabled = await get_memory_enabled(db)
        auto_extract = await get_memory_auto_extract(db)
    entries: dict[str, list[str]] = {
        "user": memory_store.read_entries("user"),
        "global": memory_store.read_entries("global"),
    }
    if project_id:
        entries["project"] = memory_store.read_entries("project", project_id=project_id)
    return MemoryView(enabled=enabled, auto_extract=auto_extract, entries=entries)


@router.get("")
async def get_memory(project_id: str | None = None) -> MemoryView:
    """Return the memory toggles + current entries per scope."""
    return await _view(project_id)


@router.patch("/settings")
async def patch_memory_settings(payload: MemorySettingsPatch) -> MemorySettings:
    """Toggle memory on/off. Only sent keys are updated."""
    async with async_unit_of_work() as db:
        if payload.enabled is not None:
            await set_memory_enabled(db, payload.enabled)
        if payload.auto_extract is not None:
            await set_memory_auto_extract(db, payload.auto_extract)
        return MemorySettings(
            enabled=await get_memory_enabled(db),
            auto_extract=await get_memory_auto_extract(db),
        )


@router.delete("/entry")
async def delete_memory_entry(payload: MemoryEntryDelete) -> MemoryView:
    """Delete the entry located by a unique ``old_text`` substring in ``target``."""
    try:
        result = memory_store.remove(
            payload.target, payload.old_text, project_id=payload.project_id, source="user"
        )
    except MemoryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=str(result.get("error", "no match")))
    return await _view(payload.project_id)


@router.delete("/scope")
async def clear_memory_scope(payload: MemoryClear) -> MemoryView:
    """Clear every entry in ``target``."""
    try:
        memory_store.clear(payload.target, project_id=payload.project_id)
    except MemoryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await _view(payload.project_id)
