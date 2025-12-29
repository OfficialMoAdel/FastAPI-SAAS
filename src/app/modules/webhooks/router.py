"""Webhooks API Router."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.dependencies import get_current_tenant, get_current_user
from src.app.core.db.database import async_get_db
from src.app.modules.tenants.models import Tenant
from src.app.modules.users.models import User
from src.app.modules.webhooks.models import WebhookDelivery, WebhookEndpoint
from src.app.modules.webhooks.schemas import (
    WebhookDeliveryListResponse,
    WebhookDeliveryResponse,
    WebhookEndpointCreate,
    WebhookEndpointListResponse,
    WebhookEndpointResponse,
    WebhookEndpointUpdate,
)
from src.app.modules.webhooks.service import WebhookService

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


@router.get("/endpoints", response_model=WebhookEndpointListResponse)
async def list_webhook_endpoints(
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    event_type: Optional[str] = Query(None, description="Filter by event type"),
    limit: int = Query(100, ge=1, le=1000, description="Page size"),
    offset: int = Query(0, ge=0, description="Page offset"),
    db: AsyncSession = Depends(async_get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
) -> WebhookEndpointListResponse:
    """List all webhook endpoints for the current tenant."""
    query = select(WebhookEndpoint).where(WebhookEndpoint.tenant_id == current_tenant.id)

    if is_active is not None:
        query = query.where(WebhookEndpoint.is_active == is_active)
    if event_type:
        query = query.where(WebhookEndpoint.event_type.contains([event_type]))

    query = query.order_by(WebhookEndpoint.created_at.desc())

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    # Apply pagination
    query = query.limit(limit).offset(offset)

    result = await db.execute(query)
    endpoints = result.scalars().all()

    return WebhookEndpointListResponse(
        items=[WebhookEndpointResponse.model_validate(e) for e in endpoints],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/endpoints", response_model=WebhookEndpointResponse, status_code=201)
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
        url=request.url,
        event_types=request.event_types,
        secret=request.secret,
    )

    return WebhookEndpointResponse.model_validate(endpoint)


@router.get("/endpoints/{endpoint_id}", response_model=WebhookEndpointResponse)
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


@router.patch("/endpoints/{endpoint_id}", response_model=WebhookEndpointResponse)
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


@router.delete("/endpoints/{endpoint_id}", status_code=204)
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


@router.get("/deliveries", response_model=WebhookDeliveryListResponse)
async def list_webhook_deliveries(
    endpoint_id: Optional[UUID] = Query(None, description="Filter by endpoint ID"),
    status: Optional[str] = Query(None, description="Filter by status"),
    event_type: Optional[str] = Query(None, description="Filter by event type"),
    start_date: Optional[datetime] = Query(None, description="Filter from date"),
    end_date: Optional[datetime] = Query(None, description="Filter to date"),
    limit: int = Query(100, ge=1, le=1000, description="Page size"),
    offset: int = Query(0, ge=0, description="Page offset"),
    db: AsyncSession = Depends(async_get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
) -> WebhookDeliveryListResponse:
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
    query = query.limit(limit).offset(offset)

    result = await db.execute(query)
    deliveries = result.scalars().all()

    return WebhookDeliveryListResponse(
        items=[WebhookDeliveryResponse.model_validate(d) for d in deliveries],
        total=total,
        limit=limit,
        offset=offset,
    )


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
