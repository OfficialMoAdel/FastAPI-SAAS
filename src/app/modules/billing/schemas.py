"""Billing & Payments Pydantic schemas.

Request/response models for Stripe settings, subscription plans,
products, pricing, user subscriptions, and billing records.
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from src.app.modules._base.schemas import BaseCreate, BaseList, BaseRead, BaseUpdate

# =============================================================================
# STRIPE SETTINGS SCHEMAS
# =============================================================================


class StripeSettingsCreate(BaseCreate):
    """Schema for creating Stripe settings (internal use)."""

    stripe_customer_id: str | None = None
    stripe_account_id: str | None = None
    # P1-8: per-tenant webhook_secret removed — verification uses the GLOBAL
    # platform secret (STRIPE_WEBHOOK_SECRET); see webhook/ingest.py.
    is_active: bool = False


class StripeSettingsCreateInternal(StripeSettingsCreate):
    """Internal schema for creating Stripe settings with tenant_id and encrypted fields."""

    tenant_id: UUID
    # BUG FIX: encrypt_column returns a hex-encoded str, not bytes.
    encrypted_secret_key: str | None = None
    updated_at: datetime | None = None


class StripeSettingsUpdate(BaseUpdate):
    """Schema for updating Stripe settings."""

    stripe_secret_key: Annotated[str | None, Field(min_length=1, max_length=255)] = None
    is_active: bool | None = None


class StripeSettingsRead(BaseRead):
    """Schema for reading Stripe settings (excludes secrets)."""

    stripe_customer_id: str | None
    stripe_account_id: str | None
    is_active: bool
    # Note: encrypted_secret_key is NOT exposed


class StripeSettingsReadFull(StripeSettingsRead):
    """Schema for reading Stripe settings with masked secret presence."""

    has_secret_key: bool = False


# =============================================================================
# SUBSCRIPTION PLAN SCHEMAS
# =============================================================================


class SubscriptionPlanCreate(BaseCreate):
    """Schema for creating a subscription plan."""

    name: Annotated[str, Field(min_length=1, max_length=100)]
    description: str | None = None
    stripe_price_id_monthly: Annotated[str | None, Field(max_length=255)] = None
    stripe_price_id_yearly: Annotated[str | None, Field(max_length=255)] = None
    price_monthly_cents: Annotated[int, Field(gt=0)]
    price_yearly_cents: Annotated[int, Field(gt=0)]
    currency: Annotated[str, Field(min_length=3, max_length=3)] = "usd"
    features: list[str] = []
    sort_order: int = 0
    is_popular: bool = False
    is_active: bool = True


class SubscriptionPlanCreateInternal(SubscriptionPlanCreate):
    """Internal schema for creating a subscription plan with tenant_id."""

    tenant_id: UUID


class SubscriptionPlanUpdate(BaseUpdate):
    """Schema for updating a subscription plan."""

    name: Annotated[str | None, Field(min_length=1, max_length=100)] = None
    description: str | None = None
    stripe_price_id_monthly: str | None = None
    stripe_price_id_yearly: str | None = None
    price_monthly_cents: Annotated[int | None, Field(gt=0)] = None
    price_yearly_cents: Annotated[int | None, Field(gt=0)] = None
    currency: Annotated[str | None, Field(min_length=3, max_length=3)] = None
    features: list[str] | None = None
    sort_order: int | None = None
    is_popular: bool | None = None
    is_active: bool | None = None


class SubscriptionPlanRead(BaseRead):
    """Schema for reading a subscription plan."""

    tenant_id: UUID
    name: str
    description: str | None
    stripe_price_id_monthly: str | None
    stripe_price_id_yearly: str | None
    price_monthly_cents: int
    price_yearly_cents: int
    currency: str
    features: list[str]
    sort_order: int
    is_popular: bool
    is_active: bool


# =============================================================================
# PRODUCT SCHEMAS
# =============================================================================


class ProductCreate(BaseCreate):
    """Schema for creating a product."""

    name: Annotated[str, Field(min_length=1, max_length=100)]
    description: str | None = None
    stripe_product_id: Annotated[str | None, Field(max_length=255)] = None
    is_active: bool = True


class ProductCreateInternal(ProductCreate):
    """Internal schema for creating a product with tenant_id."""

    tenant_id: UUID


class ProductUpdate(BaseUpdate):
    """Schema for updating a product."""

    name: Annotated[str | None, Field(min_length=1, max_length=100)] = None
    description: str | None = None
    stripe_product_id: str | None = None
    is_active: bool | None = None


class ProductRead(BaseRead):
    """Schema for reading a product."""

    tenant_id: UUID
    name: str
    description: str | None
    stripe_product_id: str | None
    is_active: bool


class ProductReadWithPricing(ProductRead):
    """Schema for reading a product with its pricing tiers."""

    pricing_tiers: list["PricingRead"] = []


# =============================================================================
# PRICING SCHEMAS
# =============================================================================


PricingInterval = Literal["monthly", "yearly", "one_time"]


class PricingCreate(BaseCreate):
    """Schema for creating a pricing tier."""

    name: Annotated[str, Field(min_length=1, max_length=100)]
    stripe_price_id: Annotated[str | None, Field(max_length=255)] = None
    amount_cents: Annotated[int, Field(ge=0)]
    currency: Annotated[str, Field(min_length=3, max_length=3)] = "usd"
    interval: PricingInterval
    features: list[str] = []
    is_active: bool = True


class PricingCreateInternal(PricingCreate):
    """Internal schema for creating a pricing tier with product_id."""

    product_id: int


class PricingUpdate(BaseUpdate):
    """Schema for updating a pricing tier."""

    name: Annotated[str | None, Field(min_length=1, max_length=100)] = None
    stripe_price_id: str | None = None
    amount_cents: Annotated[int | None, Field(ge=0)] = None
    currency: Annotated[str | None, Field(min_length=3, max_length=3)] = None
    interval: PricingInterval | None = None
    features: list[str] | None = None
    is_active: bool | None = None


class PricingRead(BaseRead):
    """Schema for reading a pricing tier."""

    product_id: int
    name: str
    stripe_price_id: str | None
    amount_cents: int
    currency: str
    interval: str
    features: list[str]
    is_active: bool


# =============================================================================
# USER SUBSCRIPTION SCHEMAS
# =============================================================================


SubscriptionStatus = Literal["active", "canceled", "past_due", "expired", "trialing"]
BillingInterval = Literal["monthly", "yearly"]


class UserSubscriptionCreate(BaseCreate):
    """Schema for creating a user subscription (internal use)."""

    user_id: int
    subscription_plan_id: int
    stripe_subscription_id: str | None = None
    status: SubscriptionStatus = "active"
    billing_interval: BillingInterval
    current_period_start: datetime
    current_period_end: datetime
    cancel_at_period_end: bool = False


class UserSubscriptionCreateInternal(UserSubscriptionCreate):
    """Internal schema for creating a user subscription with tenant_id."""

    tenant_id: UUID


class UserSubscriptionUpdate(BaseUpdate):
    """Schema for updating a user subscription."""

    stripe_subscription_id: str | None = None
    status: SubscriptionStatus | None = None
    current_period_start: datetime | None = None
    current_period_end: datetime | None = None
    cancel_at_period_end: bool | None = None
    canceled_at: datetime | None = None


class UserSubscriptionRead(BaseRead):
    """Schema for reading a user subscription."""

    tenant_id: UUID
    user_id: int
    subscription_plan_id: int
    stripe_subscription_id: str | None
    status: str
    billing_interval: str
    current_period_start: datetime
    current_period_end: datetime
    cancel_at_period_end: bool
    canceled_at: datetime | None


class UserSubscriptionReadWithPlan(UserSubscriptionRead):
    """Schema for reading a user subscription with plan details."""

    subscription_plan: SubscriptionPlanRead


# =============================================================================
# BILLING RECORD SCHEMAS
# =============================================================================


BillingRecordStatus = Literal["succeeded", "failed", "pending", "refunded"]


class BillingRecordCreate(BaseCreate):
    """Schema for creating a billing record (internal use)."""

    user_id: int
    user_subscription_id: int | None = None
    stripe_payment_intent_id: str | None = None
    stripe_invoice_id: str | None = None
    stripe_refund_id: str | None = None
    amount_cents: int
    currency: str = "usd"
    status: BillingRecordStatus
    description: str | None = None


class BillingRecordCreateInternal(BillingRecordCreate):
    """Internal schema for creating a billing record with tenant_id."""

    tenant_id: UUID


class BillingRecordRead(BaseRead):
    """Schema for reading a billing record."""

    tenant_id: UUID
    user_id: int
    user_subscription_id: int | None
    stripe_payment_intent_id: str | None
    stripe_invoice_id: str | None
    stripe_refund_id: str | None
    amount_cents: int
    currency: str
    status: str
    description: str | None


# =============================================================================
# CHECKOUT SCHEMAS
# =============================================================================


from urllib.parse import urlparse

from pydantic import AnyHttpUrl, model_validator

from src.app.core.config import settings


class CheckoutCreate(BaseCreate):
    """Schema for initiating a checkout session."""

    plan_id: int
    billing_interval: BillingInterval
    success_url: AnyHttpUrl
    cancel_url: AnyHttpUrl
    # R2-2: opt-in caller-supplied idempotency key for double-click /
    # double-POST protection. The client mints one UUID per checkout intent
    # and reuses it across retries; a fresh intent mints a fresh key, so a
    # restarted checkout never replays an old session (Stripe holds keys
    # 24h — see B16 in clients/billing/stripe.py). Capped at 255 chars
    # (Stripe's limit). Omitted → per-attempt key (retry-dedupe only).
    idempotency_key: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def validate_urls(self) -> "CheckoutCreate":
        frontend_url = settings.app.FRONTEND_URL.rstrip("/") if settings.app.FRONTEND_URL else ""
        if not frontend_url:
            raise ValueError("FRONTEND_URL must be configured to validate checkout URLs")

        parsed_frontend = urlparse(frontend_url)
        for field_name in ["success_url", "cancel_url"]:
            url = getattr(self, field_name)
            if url:
                parsed_url = urlparse(str(url))
                if parsed_url.netloc != parsed_frontend.netloc:
                    raise ValueError(f"{field_name} must match frontend domain")
        return self


class CheckoutSession(BaseCreate):
    """Schema for checkout session response."""

    checkout_url: str
    session_id: str
    expires_at: datetime


# =============================================================================
# PAGINATION SCHEMAS
# =============================================================================


class SubscriptionPlanList(BaseList[SubscriptionPlanRead]):
    """Standardized paginated subscription plan list response.

    Inherits from BaseList: items, total, page, per_page, has_next, has_previous
    """

    pass


class SubscriptionPlanPage(BaseCreate):
    """Paginated list of subscription plans.

    Deprecated: Use SubscriptionPlanList for new endpoints.
    This schema is kept for backward compatibility with existing API contracts.
    """

    items: list[SubscriptionPlanRead]
    total: int
    page: int
    limit: int
    pages: int


class ProductList(BaseList[ProductRead]):
    """Standardized paginated product list response.

    Inherits from BaseList: items, total, page, per_page, has_next, has_previous
    """

    pass


class ProductPage(BaseCreate):
    """Paginated list of products.

    Deprecated: Use ProductList for new endpoints.
    This schema is kept for backward compatibility with existing API contracts.
    """

    items: list[ProductRead]
    total: int
    page: int
    limit: int
    pages: int


class BillingRecordList(BaseList[BillingRecordRead]):
    """Standardized paginated billing record list response.

    Inherits from BaseList: items, total, page, per_page, has_next, has_previous
    """

    pass


class UserSubscriptionList(BaseList[UserSubscriptionRead]):
    """Standardized paginated user subscription list response.

    Inherits from BaseList: items, total, page, per_page, has_next, has_previous
    """

    pass


class PricingList(BaseList[PricingRead]):
    """Standardized paginated pricing list response.

    Inherits from BaseList: items, total, page, per_page, has_next, has_previous
    """

    pass


class BillingRecordPage(BaseCreate):
    """Paginated list of billing records.

    Deprecated: Use BillingRecordList for new endpoints.
    This schema is kept for backward compatibility with existing API contracts.
    """

    items: list[BillingRecordRead]
    total: int
    page: int
    limit: int
    pages: int


# =============================================================================
# DELETE SCHEMAS (used for CRUD type parameters)
# =============================================================================


class StripeSettingsDelete(BaseUpdate):
    """Schema for deleting Stripe settings (placeholder for CRUD typing)."""

    pass


class SubscriptionPlanDelete(BaseUpdate):
    """Schema for deleting subscription plans (placeholder for CRUD typing)."""

    pass


class ProductDelete(BaseUpdate):
    """Schema for deleting products (placeholder for CRUD typing)."""

    pass


class PricingDelete(BaseUpdate):
    """Schema for deleting pricing (placeholder for CRUD typing)."""

    pass


class UserSubscriptionDelete(BaseUpdate):
    """Schema for deleting user subscriptions (placeholder for CRUD typing)."""

    pass


class BillingRecordDelete(BaseUpdate):
    """Schema for deleting billing records (placeholder for CRUD typing)."""

    pass


class BillingRecordUpdate(BaseUpdate):
    """Schema for updating billing records (placeholder for CRUD typing)."""

    pass


# =============================================================================
# ERROR SCHEMAS
# =============================================================================


class PaymentError(BaseCreate):
    """Schema for payment error response."""

    detail: str
    error_code: str
    decline_code: str | None = None


class PortalResponse(BaseCreate):
    """Schema for customer portal session response."""

    url: str


class WebhookResponse(BaseModel):
    """Schema for webhook response."""

    status: str
    message: str | None = None


# Resolve forward references
ProductReadWithPricing.model_rebuild()
