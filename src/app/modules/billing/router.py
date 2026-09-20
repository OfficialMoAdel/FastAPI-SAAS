"""Billing & Payments router - HTTP handlers for billing operations."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, HttpUrl
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.deps import RequestContextDep
from src.app.core.db.database import async_get_db
from src.app.core.logger import get_logger
from src.app.core.pagination import PaginationParams, build_paginated_response, get_pagination_params
from src.app.core.rbac.dependencies import requires_permission
from src.app.core.rbac.permissions import Permission
from src.app.core.security.auto_ban import increment_violations
from src.app.core.utils.ip_utils import get_client_ip
from src.app.modules.billing.checkout_service import get_checkout_service
from src.app.modules.billing.exceptions import WebhookVerificationError
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
    UserSubscriptionList,
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
from src.app.modules.billing.webhook import (
    ingest_webhook,
    verify_webhook_signature,
)
from src.app.shared.exceptions import ModelNotFoundError

router = APIRouter(prefix="/billing", tags=["billing"])
logger = get_logger(__name__)


# =============================================================================
# WEBHOOK ENDPOINT
# =============================================================================


@router.post("/webhook", response_model=WebhookResponse, status_code=status.HTTP_200_OK)
async def stripe_webhook(
    request: Request,
) -> Any:
    """Handle Stripe webhooks."""
    payload = await request.body()
    sig_header = request.headers.get("Stripe-Signature")
    client_ip = get_client_ip(request)
    try:
        return await ingest_webhook(payload, sig_header, verify_fn=verify_webhook_signature)
    except WebhookVerificationError:
        # S1 (SEC-08): per-IP invalid-signature meter via the auto-ban seam.
        # Repeated failures escalate to a ban (the ip_filter guard then blocks
        # the source entirely, bounding log/HMAC cost), while legitimate
        # Stripe retries (valid signatures) never touch this path and can
        # never be 429'd. The global BodyLimit guard already bounds the
        # body-read cost of each probe.
        logger.warning(
            "stripe_webhook_signature_invalid",
            client_ip=client_ip,
            has_signature=sig_header is not None,
            payload_size=len(payload),
        )
        # An unresolvable client IP falls back to a bounded, segregated bucket
        # rather than collapsing every anonymous source into one shared
        # counter that could trip a ban for unrelated traffic.
        accounting_key = client_ip if client_ip and client_ip != "unknown" else "unknown:no_client_ip"
        await increment_violations(accounting_key)
        raise


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
        # I13: no fabricated placeholder row — an unconfigured tenant gets the
        # canonical 404 envelope (NOT_FOUND), not id:0 / created_at:None under
        # a read schema (which poisons client caching/normalization logic).
        raise ModelNotFoundError("StripeSettings", "current tenant")
    return {
        **stripe_settings,
        "has_secret_key": bool(stripe_settings.get("encrypted_secret_key")),
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


@router.get("/subscriptions/me", response_model=UserSubscriptionList)
async def get_my_subscriptions(
    ctx: RequestContextDep,
    pagination: Annotated[PaginationParams, Depends(get_pagination_params)],
) -> Any:
    """Get current user's subscriptions (paginated)."""
    service = get_user_subscription_service(ctx.db)
    result = await service.get_user_subscriptions(ctx.user_id, page=pagination.page, limit=pagination.limit)
    data = result.get("data", [])
    total = result.get("total_count", len(data))
    return build_paginated_response(items=data, total=total, page=pagination.page, per_page=pagination.per_page)


@router.post("/subscriptions/{subscription_id}/cancel", response_model=UserSubscriptionRead)
async def cancel_subscription(
    subscription_id: int,
    ctx: RequestContextDep,
) -> Any:
    """Cancel a subscription at period end (ownership enforced in service)."""
    service = get_user_subscription_service(ctx.db)
    return await service.cancel_subscription_for_user(
        subscription_id, user_id=ctx.user_id, is_superuser=ctx.is_superuser
    )


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
    return await service.create_session(user_id=ctx.user_id, user_email=ctx.user.email, data=data)


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
    return await service.get_record_for_user(record_id, user_id=ctx.user_id, is_superuser=ctx.is_superuser)
