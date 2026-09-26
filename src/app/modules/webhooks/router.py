"""Webhooks API Router."""

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from src.app.api.deps import RequestContextDep
from src.app.core.pagination import MAX_PER_PAGE, build_paginated_response
from src.app.core.rbac import Permission, requires_permission
from src.app.modules.webhooks.models import WebhookDeliveryStatus
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
from src.app.shared.exceptions import ModelNotFoundError

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# Route template for rate limit lookup - must match the registered API path.
_WEBHOOK_TEST_RATE_LIMIT_PATH = "/api/v1/webhooks/{endpoint_id}/test"


@router.get(
    "",
    response_model=WebhookEndpointList,
    dependencies=[Depends(requires_permission(Permission.WEBHOOKS_READ))],
)
async def list_webhook_endpoints(
    ctx: RequestContextDep,
    is_active: bool | None = Query(None, description="Filter by active status"),
    event_type: str | None = Query(None, description="Filter by event type"),
    page: int = Query(1, ge=1, description="Page number"),
    # P2 pagination-drift fix: cap at the shared MAX_PER_PAGE (100), not 1000.
    per_page: int = Query(100, ge=1, le=MAX_PER_PAGE, description="Page size"),
) -> Any:
    """List all webhook endpoints for the current tenant."""
    from src.app.modules.webhooks.service import WebhookService

    service = WebhookService(ctx.db)
    endpoints, total = await service.get_endpoints(
        tenant_id=ctx.tenant_id,
        is_active=is_active,
        event_type=event_type,
        page=page,
        per_page=per_page,
    )

    return build_paginated_response(items=endpoints, total=total, page=page, per_page=per_page)


@router.post(
    "",
    response_model=WebhookEndpointCreateResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requires_permission(Permission.WEBHOOKS_CREATE))],
)
async def create_webhook_endpoint(
    ctx: RequestContextDep,
    request: WebhookEndpointCreate,
) -> Any:
    """
    Create a new webhook endpoint.

    The secret will be generated automatically if not provided.
    Use this secret to verify webhook signatures on your endpoint.
    """
    service = WebhookService(ctx.db)

    endpoint = await service.create_endpoint(
        tenant_id=ctx.tenant_id,
        url=str(request.url),
        event_type=request.event_type,
        secret=request.secret,
        # W7 FIX: the router previously mapped request.secret onto this
        # parameter, silently ignoring the dedicated signing_secret field.
        signing_secret=request.signing_secret,
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
    ctx: RequestContextDep,
    endpoint_id: UUID | None = Query(None, description="Filter by endpoint ID"),
    delivery_status: WebhookDeliveryStatus | None = Query(None, alias="status", description="Filter by status"),
    event_type: str | None = Query(None, description="Filter by event type"),
    start_date: datetime | None = Query(None, description="Filter from date"),
    end_date: datetime | None = Query(None, description="Filter to date"),
    page: int = Query(1, ge=1, description="Page number"),
    # P2 pagination-drift fix: cap at the shared MAX_PER_PAGE (100), not 1000.
    per_page: int = Query(100, ge=1, le=MAX_PER_PAGE, description="Page size"),
) -> Any:
    """
    List webhook delivery history.

    Returns delivery attempts with status, response codes, and retry information.
    """
    from src.app.modules.webhooks.service import WebhookService

    service = WebhookService(ctx.db)
    deliveries, total = await service.get_deliveries(
        tenant_id=ctx.tenant_id,
        endpoint_id=endpoint_id,
        delivery_status=delivery_status,
        event_type=event_type,
        start_date=start_date,
        end_date=end_date,
        page=page,
        per_page=per_page,
    )

    return build_paginated_response(items=deliveries, total=total, page=page, per_page=per_page)


@router.get(
    "/{endpoint_id}",
    response_model=WebhookEndpointResponse,
    dependencies=[Depends(requires_permission(Permission.WEBHOOKS_READ))],
)
async def get_webhook_endpoint(
    ctx: RequestContextDep,
    endpoint_id: UUID,
) -> Any:
    """Get a single webhook endpoint by ID."""
    service = WebhookService(ctx.db)

    endpoint = await service.get_endpoint_by_id(
        endpoint_id=endpoint_id,
        tenant_id=ctx.tenant_id,
    )
    if not endpoint:
        raise ModelNotFoundError("Webhook endpoint", endpoint_id)

    return endpoint


@router.patch(
    "/{endpoint_id}",
    response_model=WebhookEndpointResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(requires_permission(Permission.WEBHOOKS_MANAGE))],
)
async def update_webhook_endpoint(
    ctx: RequestContextDep,
    endpoint_id: UUID,
    request: WebhookEndpointUpdate,
) -> Any:
    """Update a webhook endpoint."""
    service = WebhookService(ctx.db)

    endpoint = await service.get_endpoint_by_id(
        endpoint_id=endpoint_id,
        tenant_id=ctx.tenant_id,
    )

    if not endpoint:
        raise ModelNotFoundError("Webhook endpoint", endpoint_id)

    # Update fields via service layer
    update_data = request.model_dump(exclude_unset=True)
    return await service.update_endpoint(endpoint, update_data)


@router.delete(
    "/{endpoint_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(requires_permission(Permission.WEBHOOKS_MANAGE))],
)
async def delete_webhook_endpoint(
    ctx: RequestContextDep,
    endpoint_id: UUID,
) -> None:
    """Delete a webhook endpoint."""
    service = WebhookService(ctx.db)

    endpoint = await service.get_endpoint_by_id(
        endpoint_id=endpoint_id,
        tenant_id=ctx.tenant_id,
    )

    if not endpoint:
        raise ModelNotFoundError("Webhook endpoint", endpoint_id)

    await service.delete_endpoint(endpoint)


@router.get(
    "/deliveries/{delivery_id}",
    response_model=WebhookDeliveryResponse,
    dependencies=[Depends(requires_permission(Permission.WEBHOOKS_READ))],
)
async def get_webhook_delivery(
    ctx: RequestContextDep,
    delivery_id: UUID,
) -> Any:
    """Get details of a specific webhook delivery attempt."""
    service = WebhookService(ctx.db)

    delivery = await service.get_delivery_by_id(
        delivery_id=delivery_id,
        tenant_id=ctx.tenant_id,
    )

    if not delivery:
        raise ModelNotFoundError("Webhook delivery", delivery_id)

    return delivery


@router.post(
    "/deliveries/{delivery_id}/retry",
    response_model=WebhookDeliveryResponse,
    dependencies=[Depends(requires_permission(Permission.WEBHOOKS_MANAGE))],
)
async def retry_webhook_delivery(
    ctx: RequestContextDep,
    delivery_id: UUID,
) -> Any:
    """
    Manually retry a failed webhook delivery.

    Only failed deliveries can be retryable.
    """
    service = WebhookService(ctx.db)

    delivery = await service.get_delivery_by_id(
        delivery_id=delivery_id,
        tenant_id=ctx.tenant_id,
    )

    if not delivery:
        raise ModelNotFoundError("Webhook delivery", delivery_id)

    # The retryable precondition is service-owned (WebhookService.retry_delivery),
    # so it applies to every caller, not only to this router.
    await service.retry_delivery(delivery.id)

    await ctx.db.refresh(delivery)
    return delivery


@router.post(
    "/{endpoint_id}/test",
    response_model=WebhookDeliveryResponse,
    dependencies=[Depends(requires_permission(Permission.WEBHOOKS_MANAGE))],
)
async def test_webhook_endpoint(
    ctx: RequestContextDep,
    endpoint_id: UUID,
    request: WebhookTestRequest,
) -> Any:
    """
    Test a webhook endpoint by sending a simulated event.

    This simulated event is recorded as a regular delivery attempt.
    """
    # Rate limiting (service-owned; check precedes endpoint lookup to
    # prevent timing-based enumeration — 404s still consume quota).
    service = WebhookService(ctx.db)
    await service.check_test_rate_limit(ctx.tenant_id, ctx.user)

    endpoint = await service.get_endpoint_by_id(
        endpoint_id=endpoint_id,
        tenant_id=ctx.tenant_id,
    )

    if not endpoint:
        raise ModelNotFoundError("Webhook endpoint", endpoint_id)

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
    ctx: RequestContextDep,
    endpoint_id: UUID,
) -> Any:
    """Get signing-secret METADATA for a webhook endpoint (admin only).

    S-5 FIX: the plaintext secret is NO LONGER returned here. Storing an
    encrypted-at-rest secret but serving it back over the API defeats the
    encryption. The plaintext is shown exactly once — in the create/rotate
    responses. This endpoint now returns a masked preview so admins can
    confirm WHICH secret is configured, plus rotation guidance.
    """
    service = WebhookService(ctx.db)
    result = await service.get_signing_secret(
        endpoint_id=endpoint_id,
        tenant_id=ctx.tenant_id,
    )

    if not result["signing_secret"]:
        raise ModelNotFoundError("Webhook signing secret", endpoint_id)

    secret_value = result["signing_secret"]
    masked = f"{secret_value[:6]}…{secret_value[-4:]}" if len(secret_value) > 12 else "******"

    return SigningSecretViewResponse(
        webhook_endpoint_id=result["webhook_endpoint_id"],
        signing_secret=masked,
        created_at=result["created_at"],
        message=(
            "Secret masked for security. The plaintext is shown once at creation "
            "or rotation. If lost, rotate via POST /{endpoint_id}/signing-secret/rotate."
        ),
    )


@router.post(
    "/{endpoint_id}/signing-secret/rotate",
    response_model=SigningSecretRotateResponse,
    dependencies=[Depends(requires_permission(Permission.WEBHOOKS_MANAGE))],
)
async def rotate_signing_secret(
    ctx: RequestContextDep,
    endpoint_id: UUID,
    request: SigningSecretRotateRequest,
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
    service = WebhookService(ctx.db)
    result = await service.rotate_signing_secret(
        endpoint_id=endpoint_id,
        tenant_id=ctx.tenant_id,
        grace_period_seconds=request.grace_period_seconds,
    )

    return SigningSecretRotateResponse(
        webhook_endpoint_id=result["webhook_endpoint_id"],
        new_signing_secret=result["new_signing_secret"],
        rotated_at=result.get("rotated_at"),
        old_secret_expires_at=result["old_secret_expires_at"],
        grace_period_seconds=result["grace_period_seconds"],
    )
