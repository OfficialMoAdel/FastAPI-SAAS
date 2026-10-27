"""Audit Logs API Router."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from src.app.api.deps import RequestContextDep
from src.app.core.pagination import (
    DEFAULT_PER_PAGE,
    MAX_PER_PAGE,
    PaginationParams,
    build_paginated_response,
    get_pagination_params,
)
from src.app.core.rbac import Permission, requires_permission
from src.app.modules.audit_logs.schemas import (
    AuditExportCriteria,
    AuditLogKeysetPage,
    AuditLogList,
    AuditLogResponse,
    ExportCountResponse,
)
from src.app.modules.audit_logs.service import EXPORT_MAX_RECORDS, AuditFilter, AuditLogService
from src.app.shared.exceptions import ModelNotFoundError, UnprocessableRequestError

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


# Declared BEFORE ``/{log_id}`` on purpose: FastAPI matches routes in
# declaration order, so a keyset route declared after the UUID path parameter
# would be captured by it and every call would 422 on "keyset" not being a
# UUID. Same reason ``/actions/list`` and ``/export`` precede it.
@router.get(
    "/keyset",
    response_model=AuditLogKeysetPage,
    dependencies=[Depends(requires_permission(Permission.AUDIT_LOGS_VIEW_ONLY))],
)
async def list_audit_logs_keyset(
    ctx: RequestContextDep,
    action: str | None = Query(None, description="Filter by action"),
    user_id: int | None = Query(None, description="Filter by user ID"),
    entity_type: str | None = Query(None, description="Filter by entity type"),
    start_date: datetime | None = Query(None, description="Filter from date"),
    end_date: datetime | None = Query(None, description="Filter to date"),
    cursor: str | None = Query(None, description="Opaque next_cursor from the previous page"),
    limit: int = Query(
        default=DEFAULT_PER_PAGE,
        ge=1,
        le=MAX_PER_PAGE,
        description=f"Page size (max {MAX_PER_PAGE})",
    ),
) -> AuditLogKeysetPage:
    """Keyset-paginated audit logs for the current tenant.

    The sibling of ``GET /audit-logs`` for deep or infinite-scroll traversal:
    every page costs a single index seek instead of OFFSET's scan-and-discard,
    so page 10_000 is as cheap as page 1. Ordering is fixed
    ``created_at DESC, id DESC`` and filters behave identically to the offset
    endpoint.

    The response carries ``next_cursor`` and ``has_next`` but deliberately NO
    ``total`` and no page number — use the offset endpoint when the UI needs
    page jumps or a count.
    """
    audit_service = AuditLogService(ctx.db)
    try:
        page = await audit_service.query_logs_keyset(
            tenant_id=ctx.tenant_id,
            user_id=user_id,
            action=action,
            entity_type=entity_type,
            start_date=start_date,
            end_date=end_date,
            cursor=cursor,
            limit=limit,
        )
    except ValueError as e:
        # A malformed cursor is a client error (422), never a 500. The pagination
        # helpers raise ValueError because core must not depend on HTTP; the
        # domain 422 lives here.
        raise UnprocessableRequestError(str(e)) from e
    return AuditLogKeysetPage(**page)


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
    f = AuditFilter(action=action, user_id=user_id, entity_type=entity_type, start_date=start_date, end_date=end_date)
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
    format: Literal["csv", "json", "jsonl"] = Query("csv", description="Export format"),
    action: str | None = Query(None, description="Filter by action"),
    user_id: int | None = Query(None, description="Filter by user ID"),
    entity_type: str | None = Query(None, description="Filter by entity type"),
    start_date: datetime | None = Query(None, description="Filter from date"),
    end_date: datetime | None = Query(None, description="Filter to date"),
) -> StreamingResponse:
    """Export up to 100,000 tenant audit logs; requires AUDIT_LOGS_DOWNLOAD."""
    criteria = AuditExportCriteria(
        tenant_id=ctx.tenant_id,
        format=format,
        action=action,
        user_id=user_id,
        entity_type=entity_type,
        start_date=start_date,
        end_date=end_date,
    )
    chunks = await AuditLogService(ctx.db).stream_export(criteria, actor=ctx.user_id)
    media_types = {"csv": "text/csv", "json": "application/json", "jsonl": "application/x-ndjson"}
    return StreamingResponse(
        chunks,
        media_type=media_types[format],
        headers={"Content-Disposition": f"attachment; filename=audit_logs.{format}"},
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
