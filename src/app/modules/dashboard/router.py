"""
Dashboard Router

Provides dashboard metrics and analytics endpoints.
"""

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.deps import get_current_user
from src.app.core.db.database import async_get_db
from src.app.modules.dashboard.schemas import (
    DashboardMetricsResponse,
    TimeRange,
)
from src.app.modules.dashboard.service import dashboard_service

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("", response_model=DashboardMetricsResponse)
async def get_dashboard_metrics(
    time_range: TimeRange = Query(
        default=TimeRange.MONTH,
        description="Time range for metrics (today, week, month, quarter, year, all)",
    ),
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(async_get_db),
):
    """Get dashboard metrics for a time range.

    Returns aggregated metrics including:
    - User metrics (total, active, new, deleted)
    - Revenue metrics (total, MRR, ARPU, subscriptions)
    - Activity metrics (logins, API calls, webhooks, failed auth)

    **Time Ranges:**
    - `today` - Metrics for today
    - `week` - Last 7 days
    - `month` - Last 30 days (default)
    - `quarter` - Last 90 days
    - `year` - Last 365 days
    - `all` - All time

    **Requires:** Authentication (admin or user with dashboard access)
    """
    tenant_id = str(current_user.get("tenant_id"))
    return await dashboard_service.get_metrics(db=db, tenant_id=tenant_id, time_range=time_range)
