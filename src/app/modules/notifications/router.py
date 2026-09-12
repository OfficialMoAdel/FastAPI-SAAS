"""Notifications API Router."""

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from src.app.api.deps import RequestContextDep
from src.app.core.rbac.dependencies import requires_permission
from src.app.core.rbac.permissions import Permission
from src.app.modules.notifications.schemas import (
    EmailTemplateOverrideCreate,
    EmailTemplateOverrideResponse,
    NotificationList,
    NotificationResponse,
)
from src.app.modules.notifications.schemas import (
    NotificationSendRequest as SendEmailRequest,
)
from src.app.modules.notifications.service import NotificationService
from src.app.shared.exceptions import ModelNotFoundError

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.post(
    "/send",
    response_model=NotificationResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(requires_permission(Permission.NOTIFICATIONS_MANAGE))],
)
async def send_notification(
    request: SendEmailRequest,
    ctx: RequestContextDep,
) -> Any:
    """
    Send an email notification.

    Accepts:
    - template_name: Name of Jinja2 template
    - to_email: Recipient email
    - context: Template variables

    Returns notification record and queues email for background delivery.
    Status 202 indicates acceptance for processing, not delivery confirmation.

    Actor attribution: the sending user (ctx.user_id) is recorded on the
    notification so history and audit remain attributable per principal.
    """
    service = NotificationService(ctx.db)

    notification = await service.send_email(
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        template_name=request.template_name,
        to_email=request.to_email,
        context=request.context,
    )

    if notification is None:
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={"message": "Notification skipped by user preferences.", "skipped": True},
        )

    return notification


@router.get(
    "",
    response_model=NotificationList,
    dependencies=[Depends(requires_permission(Permission.NOTIFICATIONS_READ))],
)
async def list_notifications(
    ctx: RequestContextDep,
    status: str | None = Query(None, description="Filter by status"),
    start_date: datetime | None = Query(None, description="Filter from date"),
    end_date: datetime | None = Query(None, description="Filter to date"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(100, ge=1, le=1000, description="Page size"),
) -> Any:
    """
    List notification history for the current tenant.

    Supports filtering by status and date range.
    """
    service = NotificationService(ctx.db)
    offset = (page - 1) * per_page

    total, notifications = await service.get_notifications(
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        status=status,
        start_date=start_date,
        end_date=end_date,
        limit=per_page,
        offset=offset,
    )

    return {
        "items": notifications,
        "total": total,
        "page": page,
        "per_page": per_page,
        "has_next": page * per_page < total,
        "has_previous": page > 1,
    }


# Email Template Override endpoints
@router.get(
    "/templates/overrides",
    response_model=list[EmailTemplateOverrideResponse],
    dependencies=[Depends(requires_permission(Permission.NOTIFICATIONS_MANAGE))],
)
async def list_template_overrides(
    ctx: RequestContextDep,
    limit: int = Query(100, ge=1, le=1000, description="Page size"),
    offset: int = Query(0, ge=0, description="Page offset"),
) -> Any:
    """List all email template overrides for the current tenant with pagination."""
    service = NotificationService(ctx.db)
    overrides = await service.list_template_overrides(ctx.tenant_id, limit, offset)

    return overrides


@router.post(
    "/templates/overrides",
    response_model=EmailTemplateOverrideResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requires_permission(Permission.NOTIFICATIONS_MANAGE))],
)
async def create_template_override(
    request: EmailTemplateOverrideCreate,
    ctx: RequestContextDep,
) -> Any:
    """Create or update an email template override for the current tenant."""
    service = NotificationService(ctx.db)

    override = await service.create_template_override(
        tenant_id=ctx.tenant_id,
        template_name=request.template_name,
        subject_override=request.subject_override,
        content=request.content,
    )

    return override


@router.delete(
    "/templates/overrides/{template_name}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(requires_permission(Permission.NOTIFICATIONS_MANAGE))],
)
async def delete_template_override(
    template_name: str,
    ctx: RequestContextDep,
) -> None:
    """Delete an email template override (revert to default template)."""
    service = NotificationService(ctx.db)
    deleted = await service.delete_template_override(ctx.tenant_id, template_name)

    if not deleted:
        raise ModelNotFoundError("Email template override", template_name)


@router.get(
    "/{notification_id}",
    response_model=NotificationResponse,
    dependencies=[Depends(requires_permission(Permission.NOTIFICATIONS_READ))],
)
async def get_notification(
    notification_id: UUID,
    ctx: RequestContextDep,
) -> Any:
    """Get a single notification by ID."""
    service = NotificationService(ctx.db)
    notification = await service.get_notification(
        notification_id=notification_id,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
    )

    if not notification:
        raise ModelNotFoundError("Notification", notification_id)

    return notification
