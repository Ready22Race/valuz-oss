"""HTTP surface for memory management (memory-system-design §11 / P2 #6).

Lets the UI inspect what the agent remembers, prune individual entries or clear
a scope, and toggle the memory system on/off. Single-user OSS: the memory files
live under the shared data dir, so these endpoints scope by ``target`` (+ an
optional ``project_id``) rather than per-user; the toggle reads/writes run under
the request's auth context like the other preferences.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from valuz_agent.api.deps import get_current_user_id
from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.memory import Target, memory_store
from valuz_agent.modules.memory.service import MemoryError
from valuz_agent.modules.settings.preferences import (
    get_memory_auto_extract,
    get_memory_custom_instructions,
    get_memory_enabled,
    set_memory_auto_extract,
    set_memory_custom_instructions,
    set_memory_enabled,
)

router = APIRouter(prefix="/v1/memory", tags=["memory"])


class MemoryView(BaseModel):
    enabled: bool
    auto_extract: bool
    # Global reviewer guidance appended to the background extractor prompt
    # (empty = off). See memory-system-design §7.4.
    custom_instructions: str
    # Entries per scope, keyed by target (user / global / project-when-bound).
    entries: dict[str, list[str]]


class MemorySettings(BaseModel):
    enabled: bool
    auto_extract: bool
    custom_instructions: str


class MemorySettingsPatch(BaseModel):
    enabled: bool | None = None
    auto_extract: bool | None = None
    custom_instructions: str | None = None


class MemoryEntryDelete(BaseModel):
    target: Target
    old_text: str
    project_id: str | None = None


class MemoryClear(BaseModel):
    target: Target
    project_id: str | None = None


async def _view(project_id: str | None, user_id: str) -> MemoryView:
    async with async_unit_of_work(commit=False) as db:
        enabled = await get_memory_enabled(db, user_id=user_id)
        auto_extract = await get_memory_auto_extract(db, user_id=user_id)
        custom_instructions = await get_memory_custom_instructions(db, user_id=user_id)
    entries: dict[str, list[str]] = {
        "user": memory_store.read_entries("user"),
        "global": memory_store.read_entries("global"),
    }
    if project_id:
        entries["project"] = memory_store.read_entries("project", project_id=project_id)
    return MemoryView(
        enabled=enabled,
        auto_extract=auto_extract,
        custom_instructions=custom_instructions,
        entries=entries,
    )


@router.get("")
async def get_memory(
    project_id: str | None = None,
    user_id: str = Depends(get_current_user_id),
) -> MemoryView:
    """Return the memory toggles + current entries per scope."""
    return await _view(project_id, user_id)


@router.patch("/settings")
async def patch_memory_settings(
    payload: MemorySettingsPatch,
    user_id: str = Depends(get_current_user_id),
) -> MemorySettings:
    """Toggle memory on/off. Only sent keys are updated."""
    async with async_unit_of_work() as db:
        if payload.enabled is not None:
            await set_memory_enabled(db, payload.enabled, user_id=user_id)
        if payload.auto_extract is not None:
            await set_memory_auto_extract(db, payload.auto_extract, user_id=user_id)
        if payload.custom_instructions is not None:
            # Setter trims + hard-caps; sending "" disables the directives.
            await set_memory_custom_instructions(
                db, payload.custom_instructions, user_id=user_id
            )
        return MemorySettings(
            enabled=await get_memory_enabled(db, user_id=user_id),
            auto_extract=await get_memory_auto_extract(db, user_id=user_id),
            custom_instructions=await get_memory_custom_instructions(db, user_id=user_id),
        )


@router.delete("/entry")
async def delete_memory_entry(
    payload: MemoryEntryDelete,
    user_id: str = Depends(get_current_user_id),
) -> MemoryView:
    """Delete the entry located by a unique ``old_text`` substring in ``target``."""
    try:
        result = memory_store.remove(
            payload.target, payload.old_text, project_id=payload.project_id, source="user"
        )
    except MemoryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=str(result.get("error", "no match")))
    return await _view(payload.project_id, user_id)


@router.delete("/scope")
async def clear_memory_scope(
    payload: MemoryClear,
    user_id: str = Depends(get_current_user_id),
) -> MemoryView:
    """Clear every entry in ``target``."""
    try:
        memory_store.clear(payload.target, project_id=payload.project_id)
    except MemoryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await _view(payload.project_id, user_id)
