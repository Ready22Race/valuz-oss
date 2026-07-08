"""HTTP routes for the marketplace — the normalized discovery/import catalog.

  GET  /v1/marketplace/categories            — category rail for one tab
  GET  /v1/marketplace/items                 — paged normalized item list
  GET  /v1/marketplace/items/{id}            — import-preview detail
  POST /v1/marketplace/items/{id}:install    — confirmed install

The frontend never calls SkillHub directly; this layer normalizes SkillHub
skills, Valuz-official skills, and curated agent / team templates into one
item shape and delegates installs to the existing pipelines. See
``docs/plans/2026-07-07-skillhub-marketplace-product-prototype.md``.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.api.deps import get_current_user_id, get_skill_service
from valuz_agent.infra.db import get_async_session
from valuz_agent.modules.agent_packs.service import AgentPackService
from valuz_agent.modules.agents.service import AgentService
from valuz_agent.modules.marketplace.models import (
    MarketplaceCategoryList,
    MarketplaceInstallResult,
    MarketplaceItemDetail,
    MarketplaceItemList,
)
from valuz_agent.modules.marketplace.service import MarketplaceService
from valuz_agent.modules.marketplace.skillhub import SkillHubClient
from valuz_agent.modules.skills.service import SkillLibraryService

router = APIRouter(tags=["marketplace"])


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _skillhub_client() -> SkillHubClient:
    """Process-wide SkillHub client so its TTL cache spans requests."""
    return SkillHubClient()


async def _get_marketplace_service(
    db: AsyncSession = Depends(get_async_session),
    skill_service: SkillLibraryService = Depends(get_skill_service),
) -> MarketplaceService:
    from valuz_agent.modules.connectors.datastore import ConnectorDatastore
    from valuz_agent.modules.connectors.service import ConnectorService

    connector_svc = ConnectorService(ConnectorDatastore(db))
    agent_svc = AgentService(db, connector_service=connector_svc)
    return MarketplaceService(
        skillhub=_skillhub_client(),
        skill_service=skill_service,
        agent_service=agent_svc,
        pack_service=AgentPackService(agent_svc),
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/v1/marketplace/categories", response_model=MarketplaceCategoryList)
async def list_marketplace_categories(
    kind: Literal["skill", "agent"] = Query(...),
    user_id: str = Depends(get_current_user_id),
    svc: MarketplaceService = Depends(_get_marketplace_service),
) -> MarketplaceCategoryList:
    """Category rail for one marketplace tab; degrades (never fails) when
    SkillHub is unreachable."""
    return await svc.list_categories(user_id, kind)


@router.get("/v1/marketplace/items", response_model=MarketplaceItemList)
async def list_marketplace_items(
    type: Literal["skill", "agent_template", "agent_team_template"] = Query(...),
    category: str | None = Query(default=None),
    subcategory: str | None = Query(default=None),
    source: Literal["skillhub", "valuz_official"] | None = Query(default=None),
    q: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=1, le=100),
    user_id: str = Depends(get_current_user_id),
    svc: MarketplaceService = Depends(_get_marketplace_service),
) -> MarketplaceItemList:
    """Paged browse over one item type. Skill results merge SkillHub (curated
    categories only) with Valuz-official skills; `degraded` marks an outage."""
    return await svc.list_items(
        user_id,
        type_=type,
        category=category,
        subcategory=subcategory,
        source=source,
        q=q,
        page=page,
        page_size=page_size,
    )


@router.get("/v1/marketplace/items/{item_id}", response_model=MarketplaceItemDetail)
async def get_marketplace_item(
    item_id: str,
    user_id: str = Depends(get_current_user_id),
    svc: MarketplaceService = Depends(_get_marketplace_service),
) -> MarketplaceItemDetail:
    """Import-preview payload (files + security for skills, roster for teams,
    instructions for agent templates). 404/502 map via the ValuzError handler."""
    return await svc.get_item(user_id, item_id)


@router.post("/v1/marketplace/items/{item_id}:install", response_model=MarketplaceInstallResult)
async def install_marketplace_item(
    item_id: str,
    user_id: str = Depends(get_current_user_id),
    svc: MarketplaceService = Depends(_get_marketplace_service),
    db: AsyncSession = Depends(get_async_session),
) -> MarketplaceInstallResult:
    """Confirmed install (the client showed the preview). Agent/team installs
    resolve runtime/model/provider/effort from the user's global defaults —
    the same resolver onboarding uses (422 when no model channel is wired);
    skill installs skip that requirement entirely."""
    runtime = provider_id = model = effort = None
    if item_id.startswith(("valuz:agent:", "valuz:team:")):
        from valuz_agent.api.routes.onboarding import _resolve_deploy_target
        from valuz_agent.modules.settings.preferences import get_default_effort

        runtime, provider_id, model = await _resolve_deploy_target(db, user_id)
        effort = await get_default_effort(db, user_id=user_id)
    return await svc.install(
        user_id,
        item_id,
        runtime=runtime,
        provider_id=provider_id,
        model=model,
        effort=effort,
    )
