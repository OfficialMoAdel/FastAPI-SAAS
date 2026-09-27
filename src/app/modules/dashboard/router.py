"""
Dashboard Router

Provides dashboard metrics and analytics endpoints.
"""

from fastapi import APIRouter, Depends, Query

from src.app.api.deps import RequestContextDep
from src.app.core.rbac import Permission, requires_permission
from src.app.modules.dashboard.schemas import (
    DashboardMetricsResponse,
    TimeRange,
)
from src.app.modules.dashboard.service import dashboard_service

# Run-3 finding users.dashboard.metrics_missing_authorization: this router was
# mounted with no dependencies= and its only route took RequestContextDep, which
# authenticates and resolves a tenant but evaluates no role. Any authenticated
# tenant member received the whole tenant's revenue/MRR/ARPU/subscription/login
# aggregates, contradicting the docstring below ("Requires: admin or user with
# dashboard access") and matching no other read endpoint in the codebase, all of
# which are permission-gated.
#
# The gate is attached to the router so a route added later inherits it. Callers
# must hold DASHBOARD_READ, or be a superuser (requires_permission bypasses for
# is_superuser). DASHBOARD_READ is a NEW slug: operators must grant it to the
# intended roles, otherwise non-superusers receive 403 — which is the intended
# fail-closed behavior, not a regression.
router = APIRouter(
    prefix="/dashboard",
    tags=["dashboard"],
    dependencies=[Depends(requires_permission(Permission.DASHBOARD_READ))],
)


@router.get("", response_model=DashboardMetricsResponse)
async def get_dashboard_metrics(
    ctx: RequestContextDep,
    time_range: TimeRange = Query(
        default=TimeRange.MONTH,
        description="Time range for metrics (today, week, month, quarter, year, all)",
    ),
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
    tenant_id = str(ctx.tenant_id)
    return await dashboard_service.get_metrics(db=ctx.db, tenant_id=tenant_id, time_range=time_range)
