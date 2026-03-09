"""Billing & Payments router - HTTP handlers for billing operations."""

from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, HttpUrl
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.dependencies import get_current_user
from src.app.core.config import settings
from src.app.core.db.database import async_get_db
from src.app.core.logger import get_logger
from src.app.core.rbac.dependencies import requires_permission
from src.app.core.rbac.permissions import Permission
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
    get_billing_record_service,
    get_checkout_service,
    get_product_service,
    get_stripe_settings_service,
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
    background_tasks: BackgroundTasks,
) -> Any:
    """
    Handle Stripe webhooks.

    Verifies signature and dispatches event handling.
    This endpoint is global for the platform.
    """
    payload = await request.body()
    sig_header = request.headers.get("Stripe-Signature")
    webhook_secret = settings.STRIPE_WEBHOOK_SECRET.get_secret_value() if settings.STRIPE_WEBHOOK_SECRET else None

    if not webhook_secret:
        logger.error("STRIPE_WEBHOOK_SECRET is not set in configuration")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Webhook secret not configured",
        )

    if not sig_header:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing Stripe-Signature header",
        )

    # Verify signature
    # Note: verification was originally synchronous (CPU-bound)
    import asyncio

    event = await asyncio.get_event_loop().run_in_executor(
        None, verify_webhook_signature, payload, sig_header, webhook_secret
    )

    # Handle event in background task (idempotency is enforced atomically in webhook worker)
    background_tasks.add_task(handle_webhook_event, event, background_tasks)

    return {"status": "success"}


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
) -> dict:
    """Get Stripe settings for the current tenant (admin only)."""
    service = get_stripe_settings_service(db)
    settings = await service.get_settings()
    if settings is None:
        return {
            "id": 0,
            "tenant_id": 0,
            "stripe_customer_id": None,
            "stripe_account_id": None,
            "is_active": False,
            "has_secret_key": False,
            "has_webhook_secret": False,
            "created_at": None,
            "updated_at": None,
        }
    return {
        **settings,
        "has_secret_key": bool(settings.get("encrypted_secret_key")),
        "has_webhook_secret": bool(settings.get("webhook_secret")),
    }


@router.patch(
    "/settings",
    response_model=StripeSettingsRead,
    dependencies=[Depends(requires_permission(Permission.MANAGE_BILLING))],
)
async def update_stripe_settings(
    data: StripeSettingsUpdate,
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict:
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
) -> dict:
    """Create a new subscription plan (admin only)."""
    service = get_subscription_plan_service(db)
    return await service.create_plan(data)


@router.get("/plans", response_model=SubscriptionPlanList)
async def list_subscription_plans(
    db: Annotated[AsyncSession, Depends(async_get_db)],
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    is_active: bool | None = Query(None, description="Filter by active status"),
) -> dict:
    """List subscription plans (public for pricing page)."""
    service = get_subscription_plan_service(db)
    result = await service.get_plans(page=page, limit=per_page, is_active=is_active)
    data = result.get("data", [])
    total = result.get("total_count", len(data))
    return {
        "items": data,
        "total": total,
        "page": page,
        "per_page": per_page,
        "has_next": page * per_page < total,
        "has_previous": page > 1,
    }


@router.get("/plans/{plan_id}", response_model=SubscriptionPlanRead)
async def get_subscription_plan(
    plan_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict:
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
) -> dict:
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
) -> dict:
    """Create a new product (admin only)."""
    service = get_product_service(db)
    return await service.create_product(data)


@router.get("/products", response_model=ProductList)
async def list_products(
    db: Annotated[AsyncSession, Depends(async_get_db)],
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    is_active: bool | None = Query(None, description="Filter by active status"),
) -> dict:
    """List products."""
    service = get_product_service(db)
    result = await service.get_products(page=page, limit=per_page, is_active=is_active)
    data = result.get("data", [])
    total = result.get("total_count", len(data))
    return {
        "items": data,
        "total": total,
        "page": page,
        "per_page": per_page,
        "has_next": page * per_page < total,
        "has_previous": page > 1,
    }


@router.get("/products/{product_id}", response_model=ProductRead)
async def get_product(
    product_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict:
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
) -> dict:
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
) -> dict:
    """Add a pricing tier to a product (admin only)."""
    service = get_product_service(db)
    return await service.add_pricing(product_id, data)


# =============================================================================
# USER SUBSCRIPTION ENDPOINTS
# =============================================================================


@router.get("/subscriptions/me", response_model=list[UserSubscriptionRead])
async def get_my_subscriptions(
    current_user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> list[UserSubscriptionRead]:
    """Get current user's subscriptions."""
    service = get_user_subscription_service(db)
    result = await service.get_user_subscriptions(current_user["id"])
    data: list[UserSubscriptionRead] = result.get("data", [])
    return data


@router.post("/subscriptions/{subscription_id}/cancel", response_model=UserSubscriptionRead)
async def cancel_subscription(
    subscription_id: int,
    current_user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict:
    """Cancel a subscription at period end."""
    service = get_user_subscription_service(db)
    subscription = await service.get_subscription(subscription_id)

    # Verify ownership
    if subscription["user_id"] != current_user["id"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to cancel this subscription",
        )

    return await service.cancel_subscription(subscription_id)


# =============================================================================
# CHECKOUT & BILLING RECORD ENDPOINTS
# =============================================================================


@router.post("/checkout", response_model=CheckoutSession)
async def create_checkout_session(
    data: CheckoutCreate,
    current_user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> CheckoutSession:
    """Create a Stripe checkout session."""
    service = get_checkout_service(db)
    return await service.create_session(user_id=current_user["id"], user_email=current_user["email"], data=data)


class PortalSessionRequest(BaseModel):
    return_url: HttpUrl


@router.post(
    "/portal",
    response_model=PortalResponse,
    dependencies=[Depends(requires_permission(Permission.MANAGE_BILLING))],
)
async def create_portal_session(
    body: PortalSessionRequest,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
) -> PortalResponse:
    """Create a Stripe Customer Portal session."""
    service = get_checkout_service(db)
    url = await service.create_portal_session(return_url=str(body.return_url))
    return PortalResponse(url=url)


@router.get("/records", response_model=BillingRecordList)
async def list_billing_records(
    current_user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    status: str | None = Query(None, description="Filter by status (succeeded, failed, pending, refunded)"),
) -> dict:
    """List billing records for the current user.

    Args:
        page: Page number (1-indexed)
        per_page: Number of records per page
        status: Optional filter by status (succeeded, failed, pending, refunded)
    """
    service = get_billing_record_service(db)
    result = await service.get_user_records(current_user["id"], page=page, limit=per_page, status=status)
    data = result.get("data", [])
    total = result.get("total_count", len(data))
    return {
        "items": data,
        "total": total,
        "page": page,
        "per_page": per_page,
        "has_next": page * per_page < total,
        "has_previous": page > 1,
    }


@router.get("/records/{record_id}", response_model=BillingRecordRead)
async def get_billing_record(
    record_id: int,
    current_user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict:
    """Get a billing record by ID."""
    service = get_billing_record_service(db)
    record = await service.get_record(record_id)

    # Verify ownership
    if record["user_id"] != current_user["id"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view this record",
        )

    return record
