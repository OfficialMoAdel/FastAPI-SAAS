"""Audit Logs API Router."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import get_current_tenant, get_current_user
from ...core.db.database import async_get_db
from ...modules.tenants.models import Tenant
from ...modules.users.models import User
from .models import AuditLog
from .schemas import (
    AuditLogListResponse,
    AuditLogResponse,
)

router = APIRouter(prefix="/audit-logs", tags=["Audit Logs"])


@router.get("", response_model=AuditLogListResponse)
async def list_audit_logs(
    action: str | None = Query(None, description="Filter by action"),
    user_id: UUID | None = Query(None, description="Filter by user ID"),
    entity_type: str | None = Query(None, description="Filter by entity type"),
    start_date: datetime | None = Query(None, description="Filter from date"),
    end_date: datetime | None = Query(None, description="Filter to date"),
    limit: int = Query(100, ge=1, le=1000, description="Page size"),
    offset: int = Query(0, ge=0, description="Page offset"),
    db: AsyncSession = Depends(async_get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
) -> AuditLogListResponse:
    """
    List audit logs for the current tenant.

    Supports filtering by:
    - Action type
    - User ID
    - Entity type
    - Date range

    Returns paginated results with total count.
    """
    # Build query with tenant isolation
    query = select(AuditLog).where(AuditLog.tenant_id == current_tenant.id)

    # Apply filters
    if action:
        query = query.where(AuditLog.action == action)
    if user_id:
        query = query.where(AuditLog.user_id == user_id)
    if entity_type:
        query = query.where(AuditLog.entity_type == entity_type)
    if start_date:
        query = query.where(AuditLog.created_at >= start_date)
    if end_date:
        query = query.where(AuditLog.created_at <= end_date)

    # Order by created_at DESC (newest first)
    query = query.order_by(AuditLog.created_at.desc())

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    # Apply pagination
    query = query.limit(limit).offset(offset)

    # Execute query
    result = await db.execute(query)
    logs = result.scalars().all()

    return AuditLogListResponse(
        items=[AuditLogResponse.model_validate(log) for log in logs],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{log_id}", response_model=AuditLogResponse)
async def get_audit_log(
    log_id: UUID,
    db: AsyncSession = Depends(async_get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
) -> AuditLogResponse:
    """
    Get a single audit log entry by ID.

    Enforces tenant isolation - only returns logs for current tenant.
    """
    query = select(AuditLog).where(
        AuditLog.id == log_id,
        AuditLog.tenant_id == current_tenant.id,
    )
    result = await db.execute(query)
    log = result.scalar_one_or_none()

    if not log:
        raise HTTPException(status_code=404, detail="Audit log not found")

    return AuditLogResponse.model_validate(log)


@router.get("/actions/list", response_model=list[str])
async def list_available_actions(
    db: AsyncSession = Depends(async_get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
) -> list[str]:
    """
    Get list of all unique action types for the current tenant.

    Useful for filtering UI.
    """
    query = select(AuditLog.action).where(AuditLog.tenant_id == current_tenant.id).distinct()
    result = await db.execute(query)
    actions = result.scalars().all()

    return sorted(actions)


# Missing import
