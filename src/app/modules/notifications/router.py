"""Notifications API Router."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.dependencies import get_current_tenant, get_current_user
from src.app.core.db.database import async_get_db
from src.app.core.rbac.dependencies import requires_permission
from src.app.core.rbac.permissions import Permission
from src.app.modules.notifications.models import Notification
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
from src.app.modules.tenants.models import Tenant
from src.app.modules.users.models import User

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.post("/send", response_model=NotificationResponse, status_code=status.HTTP_202_ACCEPTED)
async def send_notification(
    request: SendEmailRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(async_get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
) -> NotificationResponse:
    """
    Send an email notification.

    Accepts:
    - template_name: Name of Jinja2 template
    - to_email: Recipient email
    - context: Template variables

    Returns notification record and queues email for background delivery.
    Status 202 indicates acceptance for processing, not delivery confirmation.
    """
    service = NotificationService(db)

    notification = await service.send_email(
        tenant_id=current_tenant.id,
        user_id=current_user.id,
        template_name=request.template_name,
        to_email=request.to_email,
        context=request.context,
    )

    return NotificationResponse.model_validate(notification)


@router.get("", response_model=NotificationList)
async def list_notifications(
    status: str | None = Query(None, description="Filter by status"),
    start_date: datetime | None = Query(None, description="Filter from date"),
    end_date: datetime | None = Query(None, description="Filter to date"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(100, ge=1, le=1000, description="Page size"),
    db: AsyncSession = Depends(async_get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    List notification history for the current tenant.

    Supports filtering by status and date range.
    """
    service = NotificationService(db)
    offset = (page - 1) * per_page

    total, notifications = await service.get_notifications(
        tenant_id=current_tenant.id,
        status=status,
        start_date=start_date,
        end_date=end_date,
        limit=per_page,
        offset=offset
    )

    return {
        "items": [NotificationResponse.model_validate(n) for n in notifications],
        "total": total,
        "page": page,
        "per_page": per_page,
        "has_next": page * per_page < total,
        "has_previous": page > 1,
    }


@router.get("/{notification_id}", response_model=NotificationResponse)
async def get_notification(
    notification_id: UUID,
    db: AsyncSession = Depends(async_get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
) -> NotificationResponse:
    """Get a single notification by ID."""
    query = select(Notification).where(
        Notification.id == notification_id,
        Notification.tenant_id == current_tenant.id,
    )
    result = await db.execute(query)
    notification = result.scalar_one_or_none()

    if not notification:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")

    return NotificationResponse.model_validate(notification)


# Email Template Override endpoints
@router.get("/templates/overrides", response_model=list[EmailTemplateOverrideResponse])
async def list_template_overrides(
    limit: int = Query(100, ge=1, le=1000, description="Page size"),
    offset: int = Query(0, ge=0, description="Page offset"),
    db: AsyncSession = Depends(async_get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
) -> list[EmailTemplateOverrideResponse]:
    """List all email template overrides for the current tenant with pagination."""
    service = NotificationService(db)
    overrides = await service.list_template_overrides(current_tenant.id, limit, offset)

    return [EmailTemplateOverrideResponse.model_validate(o) for o in overrides]


@router.post(
    "/templates/overrides",
    response_model=EmailTemplateOverrideResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requires_permission(Permission.NOTIFICATIONS_MANAGE))],
)
async def create_template_override(
    request: EmailTemplateOverrideCreate,
    db: AsyncSession = Depends(async_get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
) -> EmailTemplateOverrideResponse:
    """Create or update an email template override for the current tenant."""
    service = NotificationService(db)

    override = await service.create_template_override(
        tenant_id=current_tenant.id,
        template_name=request.template_name,
        subject_override=request.subject_override,
        content=request.content
    )

    return EmailTemplateOverrideResponse.model_validate(override)


@router.delete(
    "/templates/overrides/{template_name}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(requires_permission(Permission.NOTIFICATIONS_MANAGE))],
)
async def delete_template_override(
    template_name: str,
    db: AsyncSession = Depends(async_get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
) -> None:
    """Delete an email template override (revert to default template)."""
    service = NotificationService(db)
    deleted = await service.delete_template_override(current_tenant.id, template_name)

    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template override not found")
