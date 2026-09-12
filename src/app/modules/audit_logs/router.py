from typing import Any

"""Audit Logs API Router."""

import csv
import io
from collections.abc import AsyncGenerator
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from src.app.api.deps import RequestContextDep
from src.app.core.json_utils import json_dumps
from src.app.core.pagination import PaginationParams, build_paginated_response, get_pagination_params
from src.app.core.rbac import Permission, requires_permission
from src.app.modules.audit_logs.models import AuditLog
from src.app.modules.audit_logs.schemas import (
    AuditLogList,
    AuditLogResponse,
    ExportCountResponse,
)
from src.app.modules.audit_logs.service import EXPORT_MAX_RECORDS, AuditLogService, _AuditFilter
from src.app.shared.exceptions import ModelNotFoundError, ValidationError

router = APIRouter(prefix="/audit-logs", tags=["audit-logs"])


@router.get(
    "",
    response_model=AuditLogList,
    dependencies=[Depends(requires_permission(Permission.AUDIT_LOGS_VIEW_ONLY))],
)
async def list_audit_logs(
    ctx: RequestContextDep,
    action: str | None = Query(None, description="Filter by action"),
    user_id: int | None = Query(None, description="Filter by user ID"),
    entity_type: str | None = Query(None, description="Filter by entity type"),
    start_date: datetime | None = Query(None, description="Filter from date"),
    end_date: datetime | None = Query(None, description="Filter to date"),
    pagination: PaginationParams = Depends(get_pagination_params),
) -> Any:
    """
    List audit logs for the current tenant.

    Supports filtering by:
    - Action type
    - User ID
    - Entity type
    - Date range

    Returns paginated results with total count.
    """
    # Get database objects via service
    audit_service = AuditLogService(ctx.db)

    # R1-04: page + total in a single query (COUNT(*) OVER window) instead of
    # two sequential round-trips. R1-10: pagination via the shared dependency
    # so per_page is capped at MAX_PER_PAGE (was le=1000, bypassing the guard).
    logs, total = await audit_service.query_logs_with_total(
        tenant_id=ctx.tenant_id,
        user_id=user_id,
        action=action,
        entity_type=entity_type,
        start_date=start_date,
        end_date=end_date,
        limit=pagination.limit,
        offset=pagination.offset,
    )

    return build_paginated_response(items=logs, total=total, page=pagination.page, per_page=pagination.per_page)


@router.get(
    "/actions/list",
    response_model=list[str],
    dependencies=[Depends(requires_permission(Permission.AUDIT_LOGS_VIEW_ONLY))],
)
async def list_available_actions(
    ctx: RequestContextDep,
) -> Any:
    """
    Get list of all unique action types for the current tenant.

    Useful for filtering UI.
    """
    # A13: logic moved to service layer
    service = AuditLogService(ctx.db)
    return await service.list_actions(ctx.tenant_id)


# ============================================================================
# Export Endpoints (Phase 7)
# ============================================================================


@router.get(
    "/export/count",
    response_model=ExportCountResponse,  # M5: Typed response schema
    dependencies=[Depends(requires_permission(Permission.AUDIT_LOGS_DOWNLOAD))],
)
async def get_export_count(
    ctx: RequestContextDep,
    action: str | None = Query(None, description="Filter by action"),
    user_id: int | None = Query(None, description="Filter by user ID"),
    entity_type: str | None = Query(None, description="Filter by entity type"),
    start_date: datetime | None = Query(None, description="Filter from date"),
    end_date: datetime | None = Query(None, description="Filter to date"),
) -> Any:
    """
    Get count of records that would be exported.

    Use this before export to check if date filtering is needed.
    """
    # A13: filter composition + counting moved to service layer
    service = AuditLogService(ctx.db)
    f = _AuditFilter(action=action, user_id=user_id, entity_type=entity_type, start_date=start_date, end_date=end_date)
    count = await service.export_count(ctx.tenant_id, f)

    return ExportCountResponse(
        count=count,
        max_export_limit=EXPORT_MAX_RECORDS,
        can_export=count <= EXPORT_MAX_RECORDS,
    )


@router.get(
    "/export",
    response_model=None,  # M6: StreamingResponse cannot use response_model; see responses parameter below
    responses={
        200: {
            "description": "Audit logs export file",
            "content": {
                "text/csv": {"schema": {"type": "string"}},
                "application/json": {"schema": {"type": "array", "items": {"type": "object"}}},
                "application/x-ndjson": {"schema": {"type": "string"}},
            },
        }
    },
    dependencies=[Depends(requires_permission(Permission.AUDIT_LOGS_DOWNLOAD))],
)
async def export_audit_logs(
    ctx: RequestContextDep,
    format: str = Query("csv", pattern="^(csv|json|jsonl)$", description="Export format"),
    action: str | None = Query(None, description="Filter by action"),
    user_id: int | None = Query(None, description="Filter by user ID"),
    entity_type: str | None = Query(None, description="Filter by entity type"),
    start_date: datetime | None = Query(None, description="Filter from date"),
    end_date: datetime | None = Query(None, description="Filter to date"),
) -> Any:
    """
    Export audit logs as CSV, JSON, or JSONL.

    Maximum 100,000 records per export. Use date filtering for larger datasets.
    Requires AUDIT_LOGS_DOWNLOAD permission.
    """
    db = ctx.db
    tenant_id = ctx.tenant_id
    user_id = ctx.user_id

    # A13: filter composition + counting moved to service layer
    service = AuditLogService(db)
    f = _AuditFilter(action=action, user_id=user_id, entity_type=entity_type, start_date=start_date, end_date=end_date)

    stmt = service.export_stmt(tenant_id, f)
    count = await service.export_count(tenant_id, f)

    if count > EXPORT_MAX_RECORDS:
        raise ValidationError(
            f"Export exceeds maximum of {EXPORT_MAX_RECORDS} records. "
            f"Found {count} records. Use date filtering to reduce the export size.",
        )

    # Order by created_at for consistent export
    stmt = stmt.order_by(AuditLog.created_at.asc())

    # Log the export event
    audit_service = AuditLogService(db)
    await audit_service.create_log(
        tenant_id=tenant_id,
        user_id=user_id,
        action="audit_logs.export",
        entity_type="audit_log",
        entity_id=None,
        details={"format": format, "record_count": count},
    )

    def _sanitize_csv_cell(value: str | None) -> str:
        """Prevent CSV formula injection by prefixing dangerous characters."""
        if not value:
            return ""
        if value[0] in ("=", "+", "-", "@", "\t", "\r"):
            return f"'{value}"
        return value

    async def generate_csv() -> AsyncGenerator[str, None]:
        """Generate CSV content."""
        output = io.StringIO()
        writer = csv.writer(output)

        # Header
        writer.writerow(
            [
                "id",
                "action",
                "user_id",
                "entity_type",
                "entity_id",
                "details",
                "ip_address",
                "user_agent",
                "created_at",
            ]
        )
        yield output.getvalue()
        output.seek(0)
        output.truncate(0)

        # Stream rows
        stream_result = await db.stream(stmt)
        async for log in stream_result.scalars():
            writer.writerow(
                [
                    str(log.id),
                    _sanitize_csv_cell(log.action),
                    str(log.user_id) if log.user_id else "",
                    _sanitize_csv_cell(log.entity_type or ""),
                    str(log.entity_id) if log.entity_id else "",
                    json_dumps(log.details, as_str=True) if log.details else "",
                    _sanitize_csv_cell(log.ip_address or ""),
                    _sanitize_csv_cell(log.user_agent or ""),
                    log.created_at.isoformat() if log.created_at else "",
                ]
            )
            yield output.getvalue()
            output.seek(0)
            output.truncate(0)

    async def generate_jsonl() -> AsyncGenerator[str, None]:
        """Generate JSONL content (one JSON object per line)."""
        stream_result = await db.stream(stmt)
        async for log in stream_result.scalars():
            line = json_dumps(
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
                },
                as_str=True,
            )
            yield str(line) + "\n"

    async def generate_json() -> AsyncGenerator[str, None]:
        """Generate JSON array content."""
        yield "[\n"
        first = True
        stream_result = await db.stream(stmt)
        async for log in stream_result.scalars():
            if not first:
                yield ",\n"
            first = False
            line = json_dumps(
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
                },
                as_str=True,
            )
            yield str(line)
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


@router.get(
    "/{log_id}",
    response_model=AuditLogResponse,
    dependencies=[Depends(requires_permission(Permission.AUDIT_LOGS_VIEW_ONLY))],
)
async def get_audit_log(
    log_id: UUID,
    ctx: RequestContextDep,
) -> Any:
    """
    Get a single audit log entry by ID.

    Enforces tenant isolation - only returns logs for current tenant.
    """
    service = AuditLogService(ctx.db)
    log = await service.get_log(log_id=log_id, tenant_id=ctx.tenant_id)
    if not log:
        raise ModelNotFoundError("Audit log", log_id)

    return log
