"""Webhooks API Router."""

from datetime import datetime
from typing import Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import HttpUrl
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.dependencies import get_current_tenant, get_current_user
from src.app.core.config import settings
from src.app.core.db.database import async_get_db
from src.app.core.rbac import Permission, requires_permission
from src.app.core.security.encryption import encrypt_data
from src.app.core.security.url_validator import SSRFProtectionError, validate_webhook_url
from src.app.modules.tenants.models import Tenant
from src.app.modules.webhooks.models import WebhookDelivery, WebhookDeliveryStatus, WebhookEndpoint
from src.app.modules.webhooks.schemas import (
    SigningSecretRotateRequest,
    SigningSecretRotateResponse,
    SigningSecretViewResponse,
    WebhookDeliveryList,
    WebhookDeliveryResponse,
    WebhookEndpointCreate,
    WebhookEndpointCreateResponse,
    WebhookEndpointList,
    WebhookEndpointResponse,
    WebhookEndpointUpdate,
    WebhookTestRequest,
)
from src.app.modules.webhooks.service import WebhookService

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# Route template for rate limit lookup - must match the registered API path.
_WEBHOOK_TEST_RATE_LIMIT_PATH = "/api/v1/webhooks/{endpoint_id}/test"


async def _validate_url_ssrf(url: str) -> None:
    """Validate a webhook URL for SSRF protection.

    Raises HTTPException(400) if the URL targets an internal/private IP
    and WEBHOOK_ALLOW_INTERNAL_URLS is False (default).

    Shared by router handlers and can be imported by the service layer.
    """
    if not settings.app.WEBHOOK_ALLOW_INTERNAL_URLS:
        try:
            await validate_webhook_url(url)
        except SSRFProtectionError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid URL: {str(e)}",
            )



@router.get(
    "",
    response_model=WebhookEndpointList,
    dependencies=[Depends(requires_permission(Permission.WEBHOOKS_READ))],
)
async def list_webhook_endpoints(
    is_active: bool | None = Query(None, description="Filter by active status"),
    event_type: str | None = Query(None, description="Filter by event type"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(100, ge=1, le=1000, description="Page size"),
    db: AsyncSession = Depends(async_get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: dict[str, Any] = Depends(get_current_user),
) -> Any:
    """List all webhook endpoints for the current tenant."""
    query = select(WebhookEndpoint).where(WebhookEndpoint.tenant_id == current_tenant.id)

    if is_active is not None:
        query = query.where(WebhookEndpoint.is_active == is_active)
    if event_type:
        query = query.where(WebhookEndpoint.event_type == event_type)

    query = query.order_by(WebhookEndpoint.created_at.desc())

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    # Apply pagination
    offset = (page - 1) * per_page
    query = query.limit(per_page).offset(offset)

    result = await db.execute(query)
    endpoints = result.scalars().all()

    return {
        "items": endpoints,
        "total": total,
        "page": page,
        "per_page": per_page,
        "has_next": page * per_page < total,
        "has_previous": page > 1,
    }


@router.post(
    "",
    response_model=WebhookEndpointCreateResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requires_permission(Permission.WEBHOOKS_CREATE))],
)
async def create_webhook_endpoint(
    request: WebhookEndpointCreate,
    db: AsyncSession = Depends(async_get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: dict[str, Any] = Depends(get_current_user),
) -> Any:
    """
    Create a new webhook endpoint.

    The secret will be generated automatically if not provided.
    Use this secret to verify webhook signatures on your endpoint.
    """
    service = WebhookService(db)

    endpoint = await service.create_endpoint(
        tenant_id=current_tenant.id,
        url=str(request.url),
        event_type=request.event_type,
        signing_secret=request.secret,
    )

    # Map the transient plaintext secrets onto the specific creation response model
    return WebhookEndpointCreateResponse(
        id=endpoint.id,
        tenant_id=endpoint.tenant_id,
        url=endpoint.url,
        event_type=endpoint.event_type,
        is_active=endpoint.is_active,
        created_at=endpoint.created_at,
        updated_at=endpoint.updated_at,
        secret=getattr(endpoint, "_plaintext_secret", ""),
        signing_secret=getattr(endpoint, "_plaintext_signing_secret", None),
    )


@router.get(
    "/deliveries",
    response_model=WebhookDeliveryList,
    dependencies=[Depends(requires_permission(Permission.WEBHOOKS_READ))],
)
async def list_webhook_deliveries_route(
    endpoint_id: UUID | None = Query(None, description="Filter by endpoint ID"),
    delivery_status: WebhookDeliveryStatus | None = Query(None, alias="status", description="Filter by status"),
    event_type: str | None = Query(None, description="Filter by event type"),
    start_date: datetime | None = Query(None, description="Filter from date"),
    end_date: datetime | None = Query(None, description="Filter to date"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(100, ge=1, le=1000, description="Page size"),
    db: AsyncSession = Depends(async_get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: dict[str, Any] = Depends(get_current_user),
) -> Any:
    """
    List webhook delivery history.

    Returns delivery attempts with status, response codes, and retry information.
    """
    query = select(WebhookDelivery).join(WebhookEndpoint).where(WebhookEndpoint.tenant_id == current_tenant.id)

    if endpoint_id:
        query = query.where(WebhookDelivery.webhook_endpoint_id == endpoint_id)
    if delivery_status:
        query = query.where(WebhookDelivery.status == delivery_status)
    if event_type:
        query = query.where(WebhookDelivery.event_type == event_type)
    if start_date:
        query = query.where(WebhookDelivery.created_at >= start_date)
    if end_date:
        query = query.where(WebhookDelivery.created_at <= end_date)

    query = query.order_by(WebhookDelivery.created_at.desc())

    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    offset = (page - 1) * per_page
    query = query.limit(per_page).offset(offset)
    result = await db.execute(query)
    deliveries = result.scalars().all()

    return {
        "items": deliveries,
        "total": total,
        "page": page,
        "per_page": per_page,
        "has_next": page * per_page < total,
        "has_previous": page > 1,
    }


@router.get(
    "/{endpoint_id}",
    response_model=WebhookEndpointResponse,
    dependencies=[Depends(requires_permission(Permission.WEBHOOKS_READ))],
)
async def get_webhook_endpoint(
    endpoint_id: UUID,
    db: AsyncSession = Depends(async_get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: dict[str, Any] = Depends(get_current_user),
) -> Any:
    """Get a single webhook endpoint by ID."""
    query = select(WebhookEndpoint).where(
        WebhookEndpoint.id == endpoint_id,
        WebhookEndpoint.tenant_id == current_tenant.id,
    )
    result = await db.execute(query)
    endpoint = result.scalar_one_or_none()

    if not endpoint:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook endpoint not found")

    return endpoint


@router.patch(
    "/{endpoint_id}",
    response_model=WebhookEndpointResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(requires_permission(Permission.WEBHOOKS_MANAGE))],
)
async def update_webhook_endpoint(
    endpoint_id: UUID,
    request: WebhookEndpointUpdate,
    db: AsyncSession = Depends(async_get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: dict[str, Any] = Depends(get_current_user),
) -> Any:
    """Update a webhook endpoint."""
    query = select(WebhookEndpoint).where(
        WebhookEndpoint.id == endpoint_id,
        WebhookEndpoint.tenant_id == current_tenant.id,
    )
    result = await db.execute(query)
    endpoint = result.scalar_one_or_none()

    if not endpoint:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook endpoint not found")

    # Update fields
    update_data = request.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        if isinstance(value, HttpUrl):
            value = str(value)
            # P1.3: Validate SSRF for URL updates
            await _validate_url_ssrf(value)

        if field in ("secret", "signing_secret") and value is not None:
            value = encrypt_data(value)
        setattr(endpoint, field, value)

    await db.commit()
    await db.refresh(endpoint)

    return endpoint


@router.delete(
    "/{endpoint_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(requires_permission(Permission.WEBHOOKS_MANAGE))],
)
async def delete_webhook_endpoint(
    endpoint_id: UUID,
    db: AsyncSession = Depends(async_get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: dict[str, Any] = Depends(get_current_user),
) -> None:
    """Delete a webhook endpoint."""
    query = select(WebhookEndpoint).where(
        WebhookEndpoint.id == endpoint_id,
        WebhookEndpoint.tenant_id == current_tenant.id,
    )
    result = await db.execute(query)
    endpoint = result.scalar_one_or_none()

    if not endpoint:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook endpoint not found")

    await db.delete(endpoint)
    await db.commit()


@router.get(
    "/deliveries/{delivery_id}",
    response_model=WebhookDeliveryResponse,
    dependencies=[Depends(requires_permission(Permission.WEBHOOKS_READ))],
)
async def get_webhook_delivery(
    delivery_id: UUID,
    db: AsyncSession = Depends(async_get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: dict[str, Any] = Depends(get_current_user),
) -> Any:
    """Get details of a specific webhook delivery attempt."""
    # Join with webhook_endpoints to enforce tenant isolation
    query = (
        select(WebhookDelivery)
        .join(WebhookEndpoint)
        .where(
            WebhookDelivery.id == delivery_id,
            WebhookEndpoint.tenant_id == current_tenant.id,
        )
    )
    result = await db.execute(query)
    delivery = result.scalar_one_or_none()

    if not delivery:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook delivery not found")

    return delivery


@router.post(
    "/deliveries/{delivery_id}/retry",
    response_model=WebhookDeliveryResponse,
    dependencies=[Depends(requires_permission(Permission.WEBHOOKS_MANAGE))],
)
async def retry_webhook_delivery(
    delivery_id: UUID,
    db: AsyncSession = Depends(async_get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: dict[str, Any] = Depends(get_current_user),
) -> Any:
    """
    Manually retry a failed webhook delivery.

    Only failed deliveries can be retried.
    """
    service = WebhookService(db)

    # Get delivery with tenant check
    query = (
        select(WebhookDelivery)
        .join(WebhookEndpoint)
        .where(
            WebhookDelivery.id == delivery_id,
            WebhookEndpoint.tenant_id == current_tenant.id,
        )
    )
    result = await db.execute(query)
    delivery = result.scalar_one_or_none()

    if not delivery:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook delivery not found")

    if delivery.status != WebhookDeliveryStatus.FAILED:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only failed deliveries can be retried")

    # Reset status and enqueue for retry
    await service.retry_delivery(delivery.id)

    await db.refresh(delivery)
    return delivery


@router.post(
    "/{endpoint_id}/test",
    response_model=WebhookDeliveryResponse,
    dependencies=[Depends(requires_permission(Permission.WEBHOOKS_MANAGE))],
)
async def test_webhook_endpoint(
    endpoint_id: UUID,
    request: WebhookTestRequest,
    db: AsyncSession = Depends(async_get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: dict[str, Any] = Depends(get_current_user),
) -> Any:
    """
    Test a webhook endpoint by sending a simulated event.

    This simulated event is recorded as a regular delivery attempt.
    """
    # Rate limiting for test endpoint
    from sqlalchemy import select

    from src.app.core.utils.rate_limit import rate_limiter
    from src.app.modules.rate_limits.models import RateLimit
    from src.app.modules.rate_limits.schemas import sanitize_path

    # Query the database directly for the tenant's rate limit for this endpoint
    tier_id = current_user.get("tier_id")
    if tier_id is None and isinstance(current_user.get("tier"), dict):
        tier_id = current_user["tier"].get("id")

    rate_limit_path = sanitize_path(_WEBHOOK_TEST_RATE_LIMIT_PATH)
    stmt = select(RateLimit).where(
        RateLimit.tier_id == tier_id,
        RateLimit.path == rate_limit_path,
    )
    rate_limit_result = await db.execute(stmt)
    rate_limit_config = rate_limit_result.scalar_one_or_none()

    # Fallback to defaults if tenant hasn't configured a specific limit
    limit = rate_limit_config.limit if rate_limit_config else 10
    period = rate_limit_config.period if rate_limit_config else 60

    # Rate limit check happens BEFORE endpoint lookup intentionally.
    # This prevents timing-based endpoint enumeration attacks, though it
    # means 404s still consume rate limit quota.
    is_limited, _ = await rate_limiter.check_rate_limit(f"webhook:test:{current_tenant.id}", limit, period)
    if is_limited:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded: {limit} tests per {period} seconds",
        )

    query = select(WebhookEndpoint).where(
        WebhookEndpoint.id == endpoint_id,
        WebhookEndpoint.tenant_id == current_tenant.id,
    )
    endpoint_result = await db.execute(query)
    endpoint = cast(WebhookEndpoint | None, endpoint_result.scalar_one_or_none())

    if not endpoint:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook endpoint not found")

    service = WebhookService(db)
    delivery = await service.trigger_endpoint(
        endpoint=endpoint,
        event_type=request.event_type,
        payload=request.payload,
    )

    return delivery


@router.get(
    "/{endpoint_id}/signing-secret",
    response_model=SigningSecretViewResponse,
    dependencies=[Depends(requires_permission(Permission.WEBHOOKS_MANAGE))],
)
async def get_signing_secret(
    endpoint_id: UUID,
    db: AsyncSession = Depends(async_get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: dict[str, Any] = Depends(get_current_user),
) -> Any:
    """Get the signing secret for a webhook endpoint (admin only).

    This endpoint allows administrators to view the signing secret
    for verification purposes. The secret should be stored securely
    and never exposed to end users.
    """
    service = WebhookService(db)
    result = await service.get_signing_secret(
        endpoint_id=endpoint_id,
        tenant_id=current_tenant.id,
    )

    if not result["signing_secret"]:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No signing secret found for this endpoint")

    return SigningSecretViewResponse(
        webhook_endpoint_id=result["webhook_endpoint_id"],
        signing_secret=result["signing_secret"],
        created_at=result["created_at"],
    )


@router.post(
    "/{endpoint_id}/signing-secret/rotate",
    response_model=SigningSecretRotateResponse,
    dependencies=[Depends(requires_permission(Permission.WEBHOOKS_MANAGE))],
)
async def rotate_signing_secret(
    endpoint_id: UUID,
    request: SigningSecretRotateRequest,
    db: AsyncSession = Depends(async_get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: dict[str, Any] = Depends(get_current_user),
) -> Any:
    """Rotate the signing secret for a webhook endpoint.

    Generates a new signing secret that takes effect immediately.
    The old secret is invalidated upon rotation.

    NOTE: The grace_period_seconds parameter is reserved for future
    grace period support but is not currently implemented.

    Parameters
    ----------
    grace_period_seconds : int
        Reserved for future grace period support (0-86400, default: 300/5min)

    Returns
    -------
    SigningSecretRotateResponse
        New signing secret and rotation timestamp
    """
    service = WebhookService(db)
    result = await service.rotate_signing_secret(
        endpoint_id=endpoint_id,
        tenant_id=current_tenant.id,
        grace_period_seconds=request.grace_period_seconds,
    )

    return SigningSecretRotateResponse(
        webhook_endpoint_id=result["webhook_endpoint_id"],
        new_signing_secret=result["new_signing_secret"],
        rotated_at=result.get("rotated_at"),
        old_secret_expires_at=result["old_secret_expires_at"],
        grace_period_seconds=result["grace_period_seconds"],
    )
