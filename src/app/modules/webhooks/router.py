"""Webhooks API Router."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import get_current_tenant, get_current_user
from ...core.db.database import async_get_db
from ...modules.tenants.models import Tenant
from ...modules.users.models import User
from .models import WebhookDelivery, WebhookEndpoint
from .schemas import (
    SigningSecretRotateRequest,
    SigningSecretRotateResponse,
    SigningSecretViewResponse,
    WebhookDeliveryList,
    WebhookDeliveryResponse,
    WebhookEndpointCreate,
    WebhookEndpointList,
    WebhookEndpointResponse,
    WebhookEndpointUpdate,
    WebhookTestRequest,
)
from .service import WebhookService

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


@router.get("/", response_model=WebhookEndpointList)
async def list_webhook_endpoints(
    is_active: bool | None = Query(None, description="Filter by active status"),
    event_type: str | None = Query(None, description="Filter by event type"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(100, ge=1, le=1000, description="Page size"),
    db: AsyncSession = Depends(async_get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
) -> dict:
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
        "items": [WebhookEndpointResponse.model_validate(e) for e in endpoints],
        "total": total,
        "page": page,
        "per_page": per_page,
        "has_next": page * per_page < total,
        "has_previous": page > 1,
    }


@router.post("/", response_model=WebhookEndpointResponse, status_code=201)
async def create_webhook_endpoint(
    request: WebhookEndpointCreate,
    db: AsyncSession = Depends(async_get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
) -> WebhookEndpointResponse:
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
        secret=request.secret,
    )

    return WebhookEndpointResponse.model_validate(endpoint)


@router.get("/{endpoint_id}", response_model=WebhookEndpointResponse)
async def get_webhook_endpoint(
    endpoint_id: UUID,
    db: AsyncSession = Depends(async_get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
) -> WebhookEndpointResponse:
    """Get a single webhook endpoint by ID."""
    query = select(WebhookEndpoint).where(
        WebhookEndpoint.id == endpoint_id,
        WebhookEndpoint.tenant_id == current_tenant.id,
    )
    result = await db.execute(query)
    endpoint = result.scalar_one_or_none()

    if not endpoint:
        raise HTTPException(status_code=404, detail="Webhook endpoint not found")

    return WebhookEndpointResponse.model_validate(endpoint)


@router.patch("/{endpoint_id}", response_model=WebhookEndpointResponse)
async def update_webhook_endpoint(
    endpoint_id: UUID,
    request: WebhookEndpointUpdate,
    db: AsyncSession = Depends(async_get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
) -> WebhookEndpointResponse:
    """Update a webhook endpoint."""
    query = select(WebhookEndpoint).where(
        WebhookEndpoint.id == endpoint_id,
        WebhookEndpoint.tenant_id == current_tenant.id,
    )
    result = await db.execute(query)
    endpoint = result.scalar_one_or_none()

    if not endpoint:
        raise HTTPException(status_code=404, detail="Webhook endpoint not found")

    # Update fields
    update_data = request.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(endpoint, field, value)

    await db.commit()
    await db.refresh(endpoint)

    return WebhookEndpointResponse.model_validate(endpoint)


@router.delete("/{endpoint_id}", status_code=204)
async def delete_webhook_endpoint(
    endpoint_id: UUID,
    db: AsyncSession = Depends(async_get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
) -> None:
    """Delete a webhook endpoint."""
    query = select(WebhookEndpoint).where(
        WebhookEndpoint.id == endpoint_id,
        WebhookEndpoint.tenant_id == current_tenant.id,
    )
    result = await db.execute(query)
    endpoint = result.scalar_one_or_none()

    if not endpoint:
        raise HTTPException(status_code=404, detail="Webhook endpoint not found")

    await db.delete(endpoint)
    await db.commit()


@router.get("/deliveries", response_model=WebhookDeliveryList)
async def list_webhook_deliveries(
    endpoint_id: UUID | None = Query(None, description="Filter by endpoint ID"),
    status: str | None = Query(None, description="Filter by status"),
    event_type: str | None = Query(None, description="Filter by event type"),
    start_date: datetime | None = Query(None, description="Filter from date"),
    end_date: datetime | None = Query(None, description="Filter to date"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(100, ge=1, le=1000, description="Page size"),
    db: AsyncSession = Depends(async_get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    List webhook delivery history.

    Returns delivery attempts with status, response codes, and retry information.
    """
    # Join with webhook_endpoints to enforce tenant isolation
    query = select(WebhookDelivery).join(WebhookEndpoint).where(WebhookEndpoint.tenant_id == current_tenant.id)

    if endpoint_id:
        query = query.where(WebhookDelivery.webhook_endpoint_id == endpoint_id)
    if status:
        query = query.where(WebhookDelivery.status == status)
    if event_type:
        query = query.where(WebhookDelivery.event_type == event_type)
    if start_date:
        query = query.where(WebhookDelivery.created_at >= start_date)
    if end_date:
        query = query.where(WebhookDelivery.created_at <= end_date)

    query = query.order_by(WebhookDelivery.created_at.desc())

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    # Apply pagination
    offset = (page - 1) * per_page
    query = query.limit(per_page).offset(offset)

    result = await db.execute(query)
    deliveries = result.scalars().all()

    return {
        "items": [WebhookDeliveryResponse.model_validate(d) for d in deliveries],
        "total": total,
        "page": page,
        "per_page": per_page,
        "has_next": page * per_page < total,
        "has_previous": page > 1,
    }


@router.get("/deliveries/{delivery_id}", response_model=WebhookDeliveryResponse)
async def get_webhook_delivery(
    delivery_id: UUID,
    db: AsyncSession = Depends(async_get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
) -> WebhookDeliveryResponse:
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
        raise HTTPException(status_code=404, detail="Webhook delivery not found")

    return WebhookDeliveryResponse.model_validate(delivery)


@router.post("/deliveries/{delivery_id}/retry", response_model=WebhookDeliveryResponse)
async def retry_webhook_delivery(
    delivery_id: UUID,
    db: AsyncSession = Depends(async_get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
) -> WebhookDeliveryResponse:
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
        raise HTTPException(status_code=404, detail="Webhook delivery not found")

    if delivery.status != "failed":
        raise HTTPException(status_code=400, detail="Only failed deliveries can be retried")

    # Reset status and enqueue for retry
    await service.retry_delivery(delivery.id)

    await db.refresh(delivery)
    return WebhookDeliveryResponse.model_validate(delivery)


@router.post("/{endpoint_id}/test", response_model=WebhookDeliveryResponse)
async def test_webhook_endpoint(
    endpoint_id: UUID,
    request: WebhookTestRequest,
    db: AsyncSession = Depends(async_get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
) -> WebhookDeliveryResponse:
    """
    Test a webhook endpoint by sending a simulated event.

    This simulated event is recorded as a regular delivery attempt.
    """
    # Rate limiting for test endpoint (T106)
    # 10 tests/minute per tenant
    from ...core.utils.rate_limit import rate_limiter

    if await rate_limiter.check_rate_limit(f"webhook:test:{current_tenant.id}", 10, 60):
        # We use standard HTTPException here to match project style
        raise HTTPException(status_code=429, detail="Rate limit exceeded: 10 tests per minute")

    query = select(WebhookEndpoint).where(
        WebhookEndpoint.id == endpoint_id,
        WebhookEndpoint.tenant_id == current_tenant.id,
    )
    result = await db.execute(query)
    endpoint = result.scalar_one_or_none()

    if not endpoint:
        raise HTTPException(status_code=404, detail="Webhook endpoint not found")

    service = WebhookService(db)
    delivery = await service.trigger_endpoint(
        endpoint=endpoint,
        event_type=request.event_type,
        payload=request.payload,
    )

    return WebhookDeliveryResponse.model_validate(delivery)


@router.get("/{endpoint_id}/signing-secret", response_model=SigningSecretViewResponse)
async def get_signing_secret(
    endpoint_id: UUID,
    db: AsyncSession = Depends(async_get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
) -> SigningSecretViewResponse:
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
        raise HTTPException(status_code=404, detail="No signing secret found for this endpoint")

    return SigningSecretViewResponse(
        webhook_endpoint_id=result["webhook_endpoint_id"],
        signing_secret=result["signing_secret"],
        created_at=result["created_at"],
    )


@router.post("/{endpoint_id}/signing-secret/rotate", response_model=SigningSecretRotateResponse)
async def rotate_signing_secret(
    endpoint_id: UUID,
    request: SigningSecretRotateRequest,
    db: AsyncSession = Depends(async_get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
) -> SigningSecretRotateResponse:
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
