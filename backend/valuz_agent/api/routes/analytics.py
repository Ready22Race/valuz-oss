from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query

from valuz_agent.api.deps import require_current_user_id
from valuz_agent.modules.analytics.datastore import AnalyticsDatastore
from valuz_agent.modules.analytics.service import AnalyticsService

router = APIRouter(prefix="/v1/analytics", tags=["analytics"])


@router.get("/usage")
async def get_usage(
    year: int = Query(default_factory=lambda: date.today().year),
    month: int = Query(default_factory=lambda: date.today().month, ge=1, le=12),
    user_id: str = Depends(require_current_user_id),
) -> dict[str, Any]:
    return await AnalyticsService(AnalyticsDatastore()).get_monthly_usage(user_id, year, month)
