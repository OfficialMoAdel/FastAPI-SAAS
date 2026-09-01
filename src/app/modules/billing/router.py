"""Billing & Payments router - HTTP handlers for billing operations."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, HttpUrl
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.deps import RequestContextDep
from src.app.core.config import settings
from src.app.core.db.database import async_get_db
from src.app.core.logger import get_logger
from src.app.core.pagination import PaginationParams, build_paginated_response, get_pagination_params
from src.app.core.rbac.dependencies import requires_permission
from src.app.core.rbac.permissions import Permission
from src.app.modules.billing.checkout_service import get_checkout_service
from src.app.modules.billing.schemas import (
    BillingRecordList,
    BillingRecordRead,
    CheckoutCreate,
    CheckoutSession,
    PortalResponse,
    PricingCreate,
    PricingRead,
    ProductCreate,
    ProductList,
    ProductRead,
    ProductUpdate,
    StripeSettingsRead,
    StripeSettingsReadFull,
    StripeSettingsUpdate,
    SubscriptionPlanCreate,
    SubscriptionPlanList,
    SubscriptionPlanRead,
    SubscriptionPlanUpdate,
    UserSubscriptionRead,
    WebhookResponse,
)
from src.app.modules.billing.service import (
    get_product_service,
    get_stripe_settings_service,
)
from src.app.modules.billing.subscription_service import (
    get_billing_record_service,
    get_subscription_plan_service,
    get_user_subscription_service,
)
from src.app.modules.billing.webhook import handle_webhook_event, verify_webhook_signature

router = APIRouter(prefix="/billing", tags=["billing"])
logger = get_logger(__name__)


# =============================================================================
# WEBHOOK ENDPOINT
# =============================================================================


@router.post("/webhook", response_model=WebhookResponse, status_code=status.HTTP_200_OK)
async def stripe_webhook(
    request: Request,
    db: AsyncSession = Depends(async_get_db),
) -> Any:
    """
    Handle Stripe webhooks.

    H1 FIX: the signature is ALWAYS verified against the global platform
    secret first. The tenant identity is derived from the *verified* event
    payload afterwards — never from a query param or unverified body metadata.
    The previous flow let an attacker influence which HMAC secret validated
    the delivery by passing ?tenant_id=... or embedding tenant_id in the raw
    body, enabling forged events for any known tenant UUID.
    """
    payload = await request.body()
    sig_header = request.headers.get("Stripe-Signature")

    if not sig_header:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing Stripe-Signature header",
        )

    webhook_secret = settings.STRIPE_WEBHOOK_SECRET.get_secret_value() if settings.STRIPE_WEBHOOK_SECRET else None
    if not webhook_secret:
        logger.error("STRIPE_WEBHOOK_SECRET is not set in configuration")
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Webhook secret not configured",
        )

    # Verify signature against the GLOBAL secret (executor: CPU-bound).
    import asyncio

    event = await asyncio.get_running_loop().run_in_executor(
        None, verify_webhook_signature, payload, sig_header, webhook_secret
    )

    # Tenant is now authoritative: extracted from the VERIFIED event object.
    _derive_and_attach_tenant(event)

    # Handle event directly so that exceptions propagate and Stripe can retry
    await handle_webhook_event(event)

    return {"status": "success"}


def _derive_and_attach_tenant(event: Any) -> None:
    """B6: attach the tenant id parsed from the verified event's metadata.

    Only used as an advisory hint for logging; handlers resolve the
    authoritative tenant from the DB record matched on the verified
    stripe_subscription_id / invoice / charge id.
    """
    obj = event.get("data", {}).get("object", {}) if isinstance(event, dict) else {}
    metadata_tenant = obj.get("metadata", {}).get("tenant_id") or obj.get("client_reference_id")
    if metadata_tenant:
        try:
            event["derived_tenant_id"] = str(UUID(str(metadata_tenant)))
        except (ValueError, AttributeError):
            pass


# =============================================================================
# STRIPE SETTINGS ENDPOINTS (Admin only)
# =============================================================================


@router.get(
    "/settings",
    response_model=StripeSettingsReadFull,
    dependencies=[Depends(requires_permission(Permission.MANAGE_BILLING))],
)
async def get_stripe_settings(
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> Any:
    """Get Stripe settings for the current tenant (admin only)."""
    service = get_stripe_settings_service(db)
    stripe_settings = await service.get_settings()
    if stripe_settings is None:
        from src.app.core.tenancy.context import get_current_tenant

        tenant = get_current_tenant()
        tenant_id = tenant.id if tenant else None
        return {
            "id": 0,
            "tenant_id": tenant_id,
            "stripe_customer_id": None,
            "stripe_account_id": None,
            "is_active": False,
            "has_secret_key": False,
            "has_webhook_secret": False,
            "created_at": None,
            "updated_at": None,
        }
    return {
        **stripe_settings,
        "has_secret_key": bool(stripe_settings.get("encrypted_secret_key")),
        "has_webhook_secret": bool(stripe_settings.get("encrypted_webhook_secret")),
    }


@router.patch(
    "/settings",
    response_model=StripeSettingsRead,
    dependencies=[Depends(requires_permission(Permission.MANAGE_BILLING))],
)
async def update_stripe_settings(
    data: StripeSettingsUpdate,
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> Any:
    """Update Stripe settings for the current tenant (admin only)."""
    service = get_stripe_settings_service(db)
    return await service.update_settings(data)


# =============================================================================
# SUBSCRIPTION PLAN ENDPOINTS
# =============================================================================


@router.post(
    "/plans",
    response_model=SubscriptionPlanRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requires_permission(Permission.MANAGE_PRODUCTS))],
)
async def create_subscription_plan(
    data: SubscriptionPlanCreate,
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> Any:
    """Create a new subscription plan (admin only)."""
    service = get_subscription_plan_service(db)
    return await service.create_plan(data)


@router.get("/plans", response_model=SubscriptionPlanList)
async def list_subscription_plans(
    db: Annotated[AsyncSession, Depends(async_get_db)],
    pagination: Annotated[PaginationParams, Depends(get_pagination_params)],
    is_active: bool | None = Query(None, description="Filter by active status"),
) -> Any:
    """List subscription plans (public for pricing page)."""
    service = get_subscription_plan_service(db)
    result = await service.get_plans(page=pagination.page, limit=pagination.limit, is_active=is_active)
    data = result.get("data", [])
    total = result.get("total_count", len(data))
    return build_paginated_response(items=data, total=total, page=pagination.page, per_page=pagination.per_page)


@router.get("/plans/{plan_id}", response_model=SubscriptionPlanRead)
async def get_subscription_plan(
    plan_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> Any:
    """Get a subscription plan by ID."""
    service = get_subscription_plan_service(db)
    return await service.get_plan(plan_id)


@router.patch(
    "/plans/{plan_id}",
    response_model=SubscriptionPlanRead,
    dependencies=[Depends(requires_permission(Permission.MANAGE_PRODUCTS))],
)
async def update_subscription_plan(
    plan_id: int,
    data: SubscriptionPlanUpdate,
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> Any:
    """Update a subscription plan (admin only)."""
    service = get_subscription_plan_service(db)
    return await service.update_plan(plan_id, data)


@router.delete(
    "/plans/{plan_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(requires_permission(Permission.MANAGE_PRODUCTS))],
)
async def delete_subscription_plan(
    plan_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> None:
    """Deactivate a subscription plan (admin only)."""
    service = get_subscription_plan_service(db)
    await service.delete_plan(plan_id)
    return None


# =============================================================================
# PRODUCT ENDPOINTS
# =============================================================================


@router.post(
    "/products",
    response_model=ProductRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requires_permission(Permission.MANAGE_PRODUCTS))],
)
async def create_product(
    data: ProductCreate,
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> Any:
    """Create a new product (admin only)."""
    service = get_product_service(db)
    return await service.create_product(data)


@router.get("/products", response_model=ProductList)
async def list_products(
    db: Annotated[AsyncSession, Depends(async_get_db)],
    pagination: Annotated[PaginationParams, Depends(get_pagination_params)],
    is_active: bool | None = Query(None, description="Filter by active status"),
) -> Any:
    """List products."""
    service = get_product_service(db)
    result = await service.get_products(page=pagination.page, limit=pagination.limit, is_active=is_active)
    data = result.get("data", [])
    total = result.get("total_count", len(data))
    return build_paginated_response(items=data, total=total, page=pagination.page, per_page=pagination.per_page)


@router.get("/products/{product_id}", response_model=ProductRead)
async def get_product(
    product_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> Any:
    """Get a product by ID."""
    service = get_product_service(db)
    return await service.get_product(product_id)


@router.patch(
    "/products/{product_id}",
    response_model=ProductRead,
    dependencies=[Depends(requires_permission(Permission.MANAGE_PRODUCTS))],
)
async def update_product(
    product_id: int,
    data: ProductUpdate,
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> Any:
    """Update a product (admin only)."""
    service = get_product_service(db)
    return await service.update_product(product_id, data)


@router.post(
    "/products/{product_id}/pricing",
    response_model=PricingRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requires_permission(Permission.MANAGE_PRODUCTS))],
)
async def add_product_pricing(
    product_id: int,
    data: PricingCreate,
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> Any:
    """Add a pricing tier to a product (admin only)."""
    service = get_product_service(db)
    return await service.add_pricing(product_id, data)


# =============================================================================
# USER SUBSCRIPTION ENDPOINTS
# =============================================================================


@router.get("/subscriptions/me", response_model=list[UserSubscriptionRead])
async def get_my_subscriptions(
    ctx: RequestContextDep,
) -> list[UserSubscriptionRead]:
    """Get current user's subscriptions."""
    service = get_user_subscription_service(ctx.db)
    result = await service.get_user_subscriptions(ctx.user_id)
    data: list[Any] = result.get("data", [])
    return data


@router.post("/subscriptions/{subscription_id}/cancel", response_model=UserSubscriptionRead)
async def cancel_subscription(
    subscription_id: int,
    ctx: RequestContextDep,
) -> Any:
    """Cancel a subscription at period end (ownership enforced in service)."""
    service = get_user_subscription_service(ctx.db)
    return await service.cancel_subscription_for_user(subscription_id, user_id=ctx.user_id)


# =============================================================================
# CHECKOUT & BILLING RECORD ENDPOINTS
# =============================================================================


@router.post("/checkout", response_model=CheckoutSession)
async def create_checkout_session(
    data: CheckoutCreate,
    ctx: RequestContextDep,
) -> CheckoutSession:
    """Create a Stripe checkout session."""
    service = get_checkout_service(ctx.db)
    return await service.create_session(user_id=ctx.user_id, user_email=ctx.user["email"], data=data)


class PortalSessionRequest(BaseModel):
    return_url: HttpUrl


@router.post(
    "/portal",
    response_model=PortalResponse,
    dependencies=[Depends(requires_permission(Permission.MANAGE_BILLING))],
)
async def create_portal_session(
    body: PortalSessionRequest,
    ctx: RequestContextDep,
) -> PortalResponse:
    """Create a Stripe Customer Portal session."""
    service = get_checkout_service(ctx.db)
    url = await service.create_portal_session(return_url=str(body.return_url))
    return PortalResponse(url=url)


@router.get("/records", response_model=BillingRecordList)
async def list_billing_records(
    ctx: RequestContextDep,
    pagination: Annotated[PaginationParams, Depends(get_pagination_params)],
    status: str | None = Query(None, description="Filter by status (succeeded, failed, pending, refunded)"),
) -> Any:
    """List billing records for the current user.

    Args:
        ctx: Request context (authenticated user, tenant, DB session)
        pagination: Standardized pagination params (R1-10)
        status: Optional filter by status (succeeded, failed, pending, refunded)
    """
    service = get_billing_record_service(ctx.db)
    result = await service.get_user_records(ctx.user_id, page=pagination.page, limit=pagination.limit, status=status)
    data = result.get("data", [])
    total = result.get("total_count", len(data))
    return build_paginated_response(items=data, total=total, page=pagination.page, per_page=pagination.per_page)


@router.get("/records/{record_id}", response_model=BillingRecordRead)
async def get_billing_record(
    record_id: int,
    ctx: RequestContextDep,
) -> Any:
    """Get a billing record by ID (ownership enforced in service)."""
    service = get_billing_record_service(ctx.db)
    return await service.get_record_for_user(record_id, user_id=ctx.user_id)
