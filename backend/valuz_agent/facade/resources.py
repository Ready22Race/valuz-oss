"""Host resource library — the stable in-process API for reading and writing the
host's resources (agents / skills / connectors / knowledge bases).

This is a **host-provided facade**: OSS owns the per-kind services (which are
heterogeneous in method names, payload shapes and return types); this layer
absorbs that heterogeneity behind one uniform interface so callers — OSS's own
features and commercial overlays alike — never touch the per-module services
directly. It is the read/write counterpart of ``ports.resource_list_hook`` (the
list-post-processing hook overlays *implement*); this one overlays *call*.

Part of the OSS↔overlay contract (importable from ``valuz_agent.facade``). The
uniform unit is a ``ResourceSnapshot`` — a self-contained, portable copy of a
resource (everything needed to recreate it elsewhere), with machine-local fields
(server ids, timestamps, local paths) deliberately excluded from ``data``.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Literal

logger = logging.getLogger(__name__)

ResourceKind = Literal["agent", "skill", "connector", "kb"]


@dataclass
class ResourceRef:
    """Lightweight pointer to a resource (what ``list`` returns)."""

    kind: ResourceKind
    key: str  # portable identity: slug for agent/skill/connector, name for kb
    name: str


@dataclass
class ResourceSnapshot:
    """A portable, self-contained copy of a resource (export/import unit).

    ``data`` carries everything needed to recreate the resource, with
    machine-local fields (ids, timestamps, local filesystem paths) excluded.
    ``files`` carries a skill's file tree (path → text content); ``None`` for
    kinds without files.
    """

    kind: ResourceKind
    key: str
    name: str
    data: dict[str, Any] = field(default_factory=dict)
    files: dict[str, str] | None = None


@asynccontextmanager
async def _use(dep_factory: Any) -> AsyncGenerator[Any, None]:
    """Drive a FastAPI-style async-generator dependency manually.

    Calls ``dep_factory()``, advances the generator once to get the yielded
    value, then closes the generator in the finally block (triggering any
    teardown / commit logic in the dependency).

    Usage::

        async with _use(get_skill_service) as svc:
            result = await svc.list_catalog("chat-default")
    """
    gen = dep_factory()
    value = await gen.__anext__()
    try:
        yield value
    finally:
        await gen.aclose()


class ResourceLibrary:
    """Uniform read/write over the host's resource kinds.

    Stateless: each method opens its own session/service internally.
    Construct directly (``ResourceLibrary()``) or via the
    ``get_resource_library`` dependency.
    """

    # ── list ──────────────────────────────────────────────────────────

    async def list(self, user_id: str, kind: ResourceKind) -> list[ResourceRef]:
        """All of the user's resources of ``kind`` as lightweight refs."""
        if kind == "agent":
            from valuz_agent.infra.db import async_unit_of_work
            from valuz_agent.modules.agents.service import AgentService

            async with async_unit_of_work() as db:
                # AgentService annotates db as sync Session but uses it async
                # (so does the OSS agents route) — OSS annotation bug, harmless here.
                rows = await AgentService(db).list_agents(user_id)  # type: ignore[arg-type]
            return [ResourceRef(kind="agent", key=r.slug, name=r.name) for r in rows]

        if kind == "skill":
            from valuz_agent.api.deps import get_skill_service

            async with _use(get_skill_service) as svc:
                cat = await svc.list_catalog("chat-default")
            return [ResourceRef(kind="skill", key=s.slug, name=s.name) for s in cat.skills]

        if kind == "connector":
            from valuz_agent.infra.db import async_unit_of_work
            from valuz_agent.modules.connectors.service import ConnectorService

            async with async_unit_of_work() as db:
                views = await ConnectorService.with_defaults(db).list_connectors(user_id)
            return [ResourceRef(kind="connector", key=v.slug, name=v.display_name) for v in views]

        if kind == "kb":
            from valuz_agent.api.deps import get_document_service

            async with _use(get_document_service) as svc:
                items = await svc.list_kbs(user_id)
            return [ResourceRef(kind="kb", key=item.name, name=item.name) for item in items]

        raise NotImplementedError(f"list({kind}) not implemented")

    # ── get ───────────────────────────────────────────────────────────

    async def get(self, user_id: str, kind: ResourceKind, key: str) -> ResourceSnapshot | None:
        """Export one resource as a portable snapshot, or ``None`` if absent."""
        if kind == "agent":
            from valuz_agent.infra.db import async_unit_of_work
            from valuz_agent.modules.agents.service import AgentNotFoundError, AgentService

            async with async_unit_of_work() as db:
                try:
                    row = await AgentService(db).get_agent(user_id, key)  # type: ignore[arg-type]
                except AgentNotFoundError:
                    return None
            return ResourceSnapshot(
                kind="agent",
                key=row.slug,
                name=row.name,
                data={
                    "slug": row.slug,
                    "name": row.name,
                    "description": row.description,
                    "instructions": row.instructions,
                    "runtime": row.runtime,
                    "model": row.model,
                    "skills": row.skills,
                    "connector_types": row.connector_types,
                    "provider_id": row.provider_id,
                    "effort": row.effort,
                    "avatar": row.avatar,
                },
            )

        if kind == "skill":
            from valuz_agent.api.deps import get_skill_service

            async with _use(get_skill_service) as svc:
                # Resolve slug → skill id via catalog
                cat = await svc.list_catalog("chat-default")
                matched = next((s for s in cat.skills if s.slug == key), None)
                if matched is None:
                    return None
                detail = await svc.get_skill_detail(matched.id)
                file_nodes = await svc.list_skill_files(matched.id)
                files: dict[str, str] = {}
                for node in file_nodes:
                    try:
                        fc = await svc.read_skill_file(matched.id, node.path)
                        files[node.path] = fc.content
                    except Exception:
                        logger.debug(
                            "ResourceLibrary.get skill=%s: could not read file %s",
                            key,
                            node.path,
                            exc_info=True,
                        )
            return ResourceSnapshot(
                kind="skill",
                key=key,
                name=detail.name,
                data={
                    "name": detail.name,
                    "description": detail.description,
                    "instructions_markdown": detail.instructions_markdown,
                    "target_scope": "user",
                },
                files=files or None,
            )

        if kind == "connector":
            from valuz_agent.infra.db import async_unit_of_work
            from valuz_agent.modules.connectors.service import ConnectorService

            async with async_unit_of_work() as db:
                svc = ConnectorService.with_defaults(db)
                views = await svc.list_connectors(user_id)
                matched = next((v for v in views if v.slug == key), None)
                if matched is None:
                    return None
                v = await svc.get_connector(user_id, matched.id)
                if v is None:
                    return None
            return ResourceSnapshot(
                kind="connector",
                key=v.slug,
                name=v.display_name,
                data={
                    "display_name": v.display_name,
                    "description": v.description,
                    "transport": v.transport,
                    "url": v.url,
                    "auth_type": v.auth_type,
                    "connector_type": v.connector_type,
                    "command": v.command,
                    "args": v.args,
                    "working_dir": v.working_dir,
                },
            )

        if kind == "kb":
            from valuz_agent.api.deps import get_document_service

            async with _use(get_document_service) as svc:
                items = await svc.list_kbs(user_id)
                matched_item = next((item for item in items if item.name == key), None)
                if matched_item is None:
                    return None
                detail = await svc.get_kb(user_id, matched_item.id)
            return ResourceSnapshot(
                kind="kb",
                key=detail.name,
                name=detail.name,
                data={
                    "name": detail.name,
                    "parser_routing": detail.parser_routing,
                    "auto_discover": getattr(detail, "auto_discover", False),
                },
            )

        raise NotImplementedError(f"get({kind}) not implemented")

    # ── save ──────────────────────────────────────────────────────────

    async def save(self, user_id: str, snapshot: ResourceSnapshot) -> ResourceRef:
        """Create the snapshot's resource locally, or update it if the key exists."""
        if snapshot.kind == "agent":
            from valuz_agent.infra.db import async_unit_of_work
            from valuz_agent.modules.agents.service import AgentService, MemberAlreadyExistsError

            async with async_unit_of_work() as db:
                svc = AgentService(db)  # type: ignore[arg-type]
                try:
                    row = await svc.create_agent(user_id, snapshot.data)
                except MemberAlreadyExistsError:
                    row = await svc.update_agent(user_id, snapshot.key, snapshot.data)
            return ResourceRef(kind="agent", key=row.slug, name=row.name)

        if snapshot.kind == "skill":
            from valuz_agent.api.deps import get_skill_service
            from valuz_agent.modules.skills.models import (
                SkillCreateRequest,
                SkillFileAction,
                SkillUpdateRequest,
            )

            data = snapshot.data
            async with _use(get_skill_service) as svc:
                # Try create; on slug-conflict fall back to update
                try:
                    view = await svc.create_skill(
                        SkillCreateRequest(
                            name=data["name"],
                            target_scope=data.get("target_scope", "user"),
                            description=data.get("description") or "",
                            instructions_markdown=data.get("instructions_markdown"),
                        )
                    )
                except Exception as exc:
                    # A skill with this name / slug already exists — resolve
                    # slug→id and update in place.
                    logger.debug(
                        "ResourceLibrary.save skill: create failed (%s), attempting update",
                        exc,
                    )
                    cat = await svc.list_catalog("chat-default")
                    slug = data["name"].lower().replace(" ", "-")
                    matched = next(
                        (s for s in cat.skills if s.slug == snapshot.key or s.slug == slug), None
                    )
                    if matched is None:
                        raise
                    view = await svc.update_skill(
                        matched.id,
                        SkillUpdateRequest(
                            name=data.get("name"),
                            description=data.get("description"),
                            instructions_markdown=data.get("instructions_markdown"),
                        ),
                    )

                # Restore files best-effort
                for path, content in (snapshot.files or {}).items():
                    try:
                        await svc.write_skill_file(
                            view.id,
                            SkillFileAction(path=path, action="create", content=content),
                        )
                    except Exception:
                        logger.warning(
                            "ResourceLibrary.save skill=%s: could not write file %s",
                            view.slug,
                            path,
                            exc_info=True,
                        )
            return ResourceRef(kind="skill", key=view.slug, name=view.name)

        if snapshot.kind == "connector":
            from valuz_agent.infra.db import async_unit_of_work
            from valuz_agent.modules.connectors.service import ConnectorService

            data = snapshot.data
            async with async_unit_of_work() as db:
                conn_svc = ConnectorService.with_defaults(db)
                # Check if slug already exists
                views = await conn_svc.list_connectors(user_id)
                existing = next((v for v in views if v.slug == snapshot.key), None)
                if existing is None:
                    view = await conn_svc.create_connector(
                        user_id,
                        display_name=data["display_name"],
                        transport=data.get("transport", "http"),
                        description=data.get("description"),
                        url=data.get("url"),
                        connector_type=data.get("connector_type", "custom"),
                        command=data.get("command"),
                        args=data.get("args"),
                        working_dir=data.get("working_dir"),
                    )
                else:
                    updated = await conn_svc.update_connector(
                        user_id,
                        existing.id,
                        display_name=data.get("display_name"),
                        description=data.get("description"),
                        url=data.get("url"),
                        command=data.get("command"),
                        args=data.get("args"),
                        working_dir=data.get("working_dir"),
                    )
                    view = updated if updated is not None else existing
            return ResourceRef(kind="connector", key=view.slug, name=view.display_name)

        if snapshot.kind == "kb":
            import os

            from valuz_agent.api.deps import get_document_service
            from valuz_agent.infra.fs_registry import fs_registry
            from valuz_agent.modules.docs.errors import KbRootDuplicated

            data = snapshot.data
            root = str(fs_registry.kb_root() / data["name"])
            os.makedirs(root, exist_ok=True)
            async with _use(get_document_service) as svc:
                try:
                    kb = await svc.create_kb(
                        user_id,
                        name=data["name"],
                        root_path=root,
                        parser_routing=data.get("parser_routing", "local_only"),
                        auto_discover=bool(data.get("auto_discover", False)),
                    )
                except KbRootDuplicated:
                    kbs = await svc.list_kbs(user_id)
                    matched_item = next((item for item in kbs if item.name == data["name"]), None)
                    if matched_item is None:
                        raise
                    kb = await svc.get_kb(user_id, matched_item.id)
            return ResourceRef(kind="kb", key=kb.name, name=kb.name)

        raise NotImplementedError(f"save({snapshot.kind}) not implemented")


async def get_resource_library() -> AsyncGenerator[ResourceLibrary, None]:
    """FastAPI dependency yielding a request-scoped ``ResourceLibrary``."""
    yield ResourceLibrary()


__all__ = [
    "ResourceKind",
    "ResourceLibrary",
    "ResourceRef",
    "ResourceSnapshot",
    "get_resource_library",
]
