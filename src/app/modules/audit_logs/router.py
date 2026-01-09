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
    user_id: int | None = Query(None, description="Filter by user ID"),
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
        page=(offset // limit) + 1,
        per_page=limit,
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


# ============================================================================
# Export Endpoints (Phase 7)
# ============================================================================

EXPORT_MAX_RECORDS = 100_000


@router.get("/export/count")
async def get_export_count(
    action: str | None = Query(None, description="Filter by action"),
    user_id: int | None = Query(None, description="Filter by user ID"),
    entity_type: str | None = Query(None, description="Filter by entity type"),
    start_date: datetime | None = Query(None, description="Filter from date"),
    end_date: datetime | None = Query(None, description="Filter to date"),
    db: AsyncSession = Depends(async_get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Get count of records that would be exported.

    Use this before export to check if date filtering is needed.
    """
    query = select(func.count()).select_from(AuditLog).where(AuditLog.tenant_id == current_tenant.id)

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

    result = await db.execute(query)
    count = result.scalar_one()

    return {
        "count": count,
        "max_export_limit": EXPORT_MAX_RECORDS,
        "can_export": count <= EXPORT_MAX_RECORDS,
    }


@router.get("/export")
async def export_audit_logs(
    format: str = Query("csv", pattern="^(csv|json|jsonl)$", description="Export format"),
    action: str | None = Query(None, description="Filter by action"),
    user_id: int | None = Query(None, description="Filter by user ID"),
    entity_type: str | None = Query(None, description="Filter by entity type"),
    start_date: datetime | None = Query(None, description="Filter from date"),
    end_date: datetime | None = Query(None, description="Filter to date"),
    db: AsyncSession = Depends(async_get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
):
    """
    Export audit logs as CSV, JSON, or JSONL.

    Maximum 100,000 records per export. Use date filtering for larger datasets.
    Requires AUDIT_LOGS_DOWNLOAD permission.
    """
    import csv
    import io
    import json as json_lib
    from collections.abc import AsyncGenerator

    from fastapi.responses import StreamingResponse

    # Build query with filters
    query = select(AuditLog).where(AuditLog.tenant_id == current_tenant.id)

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

    # Check count
    count_query = select(func.count()).select_from(query.subquery())
    count_result = await db.execute(count_query)
    count = count_result.scalar_one()

    if count > EXPORT_MAX_RECORDS:
        raise HTTPException(
            status_code=400,
            detail=f"Export exceeds maximum of {EXPORT_MAX_RECORDS} records. "
            f"Found {count} records. Use date filtering to reduce the export size.",
        )

    # Order by created_at for consistent export
    query = query.order_by(AuditLog.created_at.asc())

    # Execute query
    result = await db.execute(query)
    logs = result.scalars().all()

    # Log the export event
    from src.app.modules.audit_logs.service import log_audit_event

    await log_audit_event(
        db=db,
        tenant_id=current_tenant.id,
        user_id=current_user.id,
        action="audit_logs.export",
        entity_type="audit_log",
        entity_id=None,
        details={"format": format, "record_count": count},
    )

    async def generate_csv() -> AsyncGenerator[str, None]:
        """Generate CSV content."""
        output = io.StringIO()
        writer = csv.writer(output)

        # Header
        writer.writerow(
            ["id", "action", "user_id", "entity_type", "entity_id", "details", "ip_address", "user_agent", "created_at"]
        )
        yield output.getvalue()
        output.seek(0)
        output.truncate(0)

        # Rows
        for log in logs:
            writer.writerow(
                [
                    str(log.id),
                    log.action,
                    str(log.user_id) if log.user_id else "",
                    log.entity_type or "",
                    str(log.entity_id) if log.entity_id else "",
                    json_lib.dumps(log.details) if log.details else "",
                    log.ip_address or "",
                    log.user_agent or "",
                    log.created_at.isoformat() if log.created_at else "",
                ]
            )
            yield output.getvalue()
            output.seek(0)
            output.truncate(0)

    async def generate_jsonl() -> AsyncGenerator[str, None]:
        """Generate JSONL content (one JSON object per line)."""
        for log in logs:
            yield (
                json_lib.dumps(
                    {
                        "id": str(log.id),
                        "action": log.action,
                        "user_id": str(log.user_id) if log.user_id else None,
                        "entity_type": log.entity_type,
                        "entity_id": str(log.entity_id) if log.entity_id else None,
                        "details": log.details,
                        "ip_address": log.ip_address,
                        "user_agent": log.user_agent,
                        "created_at": log.created_at.isoformat() if log.created_at else None,
                    }
                )
                + "\n"
            )

    async def generate_json() -> AsyncGenerator[str, None]:
        """Generate JSON array content."""
        yield "[\n"
        for i, log in enumerate(logs):
            if i > 0:
                yield ",\n"
            yield json_lib.dumps(
                {
                    "id": str(log.id),
                    "action": log.action,
                    "user_id": str(log.user_id) if log.user_id else None,
                    "entity_type": log.entity_type,
                    "entity_id": str(log.entity_id) if log.entity_id else None,
                    "details": log.details,
                    "ip_address": log.ip_address,
                    "user_agent": log.user_agent,
                    "created_at": log.created_at.isoformat() if log.created_at else None,
                }
            )
        yield "\n]"

    # Generate response based on format
    if format == "csv":
        return StreamingResponse(
            generate_csv(),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=audit_logs.csv"},
        )
    elif format == "jsonl":
        return StreamingResponse(
            generate_jsonl(),
            media_type="application/x-ndjson",
            headers={"Content-Disposition": "attachment; filename=audit_logs.jsonl"},
        )
    else:  # json
        return StreamingResponse(
            generate_json(),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=audit_logs.json"},
        )
