"""Notifications API Router."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import get_current_tenant, get_current_user
from ...core.db.database import async_get_db
from ...core.rbac.dependencies import requires_permission
from ...core.rbac.permissions import Permission
from ...core.utils.cache import client as redis_client
from ...modules.tenants.models import Tenant
from ...modules.users.models import User
from .models import EmailTemplateOverride, Notification
from .schemas import (
    EmailTemplateOverrideCreate,
    EmailTemplateOverrideResponse,
    NotificationListResponse,
    NotificationResponse,
)
from .schemas import (
    NotificationSendRequest as SendEmailRequest,
)
from .service import NotificationService

router = APIRouter(prefix="/notifications", tags=["Notifications"])


@router.post("/send", response_model=NotificationResponse, status_code=202)
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


@router.get("", response_model=NotificationListResponse)
async def list_notifications(
    status: str | None = Query(None, description="Filter by status"),
    start_date: datetime | None = Query(None, description="Filter from date"),
    end_date: datetime | None = Query(None, description="Filter to date"),
    limit: int = Query(100, ge=1, le=1000, description="Page size"),
    offset: int = Query(0, ge=0, description="Page offset"),
    db: AsyncSession = Depends(async_get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
) -> NotificationListResponse:
    """
    List notification history for the current tenant.

    Supports filtering by status and date range.
    """
    query = select(Notification).where(Notification.tenant_id == current_tenant.id)

    if status:
        query = query.where(Notification.status == status)
    if start_date:
        query = query.where(Notification.created_at >= start_date)
    if end_date:
        query = query.where(Notification.created_at <= end_date)

    query = query.order_by(Notification.created_at.desc())

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    # Apply pagination
    query = query.limit(limit).offset(offset)

    result = await db.execute(query)
    notifications = result.scalars().all()

    return NotificationListResponse(
        items=[NotificationResponse.model_validate(n) for n in notifications],
        total=total,
        page=(offset // limit) + 1,
        per_page=limit,
    )


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
        raise HTTPException(status_code=404, detail="Notification not found")

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
    query = (
        select(EmailTemplateOverride)
        .where(EmailTemplateOverride.tenant_id == current_tenant.id)
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(query)
    overrides = result.scalars().all()

    return [EmailTemplateOverrideResponse.model_validate(o) for o in overrides]


@router.post(
    "/templates/overrides",
    response_model=EmailTemplateOverrideResponse,
    status_code=201,
    dependencies=[Depends(requires_permission(Permission.NOTIFICATIONS_MANAGE))],
)
async def create_template_override(
    request: EmailTemplateOverrideCreate,
    db: AsyncSession = Depends(async_get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
) -> EmailTemplateOverrideResponse:
    """Create or update an email template override for the current tenant."""
    # Check if override exists
    query = select(EmailTemplateOverride).where(
        EmailTemplateOverride.tenant_id == current_tenant.id,
        EmailTemplateOverride.template_name == request.template_name,
    )
    result = await db.execute(query)
    existing = result.scalar_one_or_none()

    if existing:
        # Update existing
        existing.subject_override = request.subject_override
        existing.content = request.content
        await db.commit()
        await db.refresh(existing)

        # Invalidate cache
        if redis_client:
            await redis_client.delete(f"tenant:{current_tenant.id}:email_template:{request.template_name}")

        return EmailTemplateOverrideResponse.model_validate(existing)

    # Create new
    override = EmailTemplateOverride(
        tenant_id=current_tenant.id,
        template_name=request.template_name,
        subject_override=request.subject_override,
        content=request.content,
    )
    db.add(override)
    await db.commit()
    await db.refresh(override)

    # Invalidate cache
    if redis_client:
        await redis_client.delete(f"tenant:{current_tenant.id}:email_template:{request.template_name}")

    return EmailTemplateOverrideResponse.model_validate(override)


@router.delete(
    "/templates/overrides/{template_name}",
    status_code=204,
    dependencies=[Depends(requires_permission(Permission.NOTIFICATIONS_MANAGE))],
)
async def delete_template_override(
    template_name: str,
    db: AsyncSession = Depends(async_get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
) -> None:
    """Delete an email template override (revert to default template)."""
    query = select(EmailTemplateOverride).where(
        EmailTemplateOverride.tenant_id == current_tenant.id,
        EmailTemplateOverride.template_name == template_name,
    )
    result = await db.execute(query)
    override = result.scalar_one_or_none()

    if not override:
        raise HTTPException(status_code=404, detail="Template override not found")

    await db.delete(override)
    await db.commit()

    # Invalidate cache
    if redis_client:
        await redis_client.delete(f"tenant:{current_tenant.id}:email_template:{template_name}")
