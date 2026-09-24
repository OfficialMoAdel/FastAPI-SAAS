"""Billing & Payments database models.

Models for Stripe integration, subscription management, products, and billing records.
All models follow row-level multi-tenancy via tenant_id foreign key.
"""

import uuid as uuid_pkg
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.app.core.db.database import Base

if TYPE_CHECKING:
    from src.app.modules.tenants.models import Tenant
    from src.app.modules.users.models import User


class StripeSettings(Base, kw_only=True):
    """Tenant-level Stripe configuration.

    One record per tenant. API keys are encrypted at rest using Fernet.
    """

    __tablename__ = "stripe_settings"

    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True, init=False)
    tenant_id: Mapped[uuid_pkg.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), index=True, unique=True, nullable=False
    )

    stripe_customer_id: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    stripe_account_id: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    encrypted_secret_key: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    # P1-8: per-tenant webhook secret omitted — single platform endpoint verifies
    # against the GLOBAL STRIPE_WEBHOOK_SECRET; see webhook/ingest.py.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default_factory=lambda: datetime.now(UTC),
        nullable=False,
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, nullable=True, onupdate=lambda: datetime.now(UTC)
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship(back_populates="stripe_settings", init=False, lazy="noload")


class SubscriptionPlan(Base, kw_only=True):
    """Subscription plan definition with pricing tiers."""

    __tablename__ = "subscription_plans"

    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True, init=False)
    tenant_id: Mapped[uuid_pkg.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), index=True, nullable=False
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    price_monthly_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    price_yearly_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)

    stripe_price_id_monthly: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    stripe_price_id_yearly: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)

    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="usd")

    features: Mapped[list] = mapped_column(JSONB, nullable=False, default_factory=list, server_default="[]")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_popular: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default_factory=lambda: datetime.now(UTC),
        nullable=False,
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, nullable=True, onupdate=lambda: datetime.now(UTC)
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship(back_populates="subscription_plans", init=False, lazy="noload")
    user_subscriptions: Mapped[list["UserSubscription"]] = relationship(
        back_populates="subscription_plan", init=False, lazy="noload"
    )


class Product(Base, kw_only=True):
    """Purchasable products for one-time or add-on purchases."""

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True, init=False)
    tenant_id: Mapped[uuid_pkg.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), index=True, nullable=False
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    stripe_product_id: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default_factory=lambda: datetime.now(UTC),
        nullable=False,
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, nullable=True, onupdate=lambda: datetime.now(UTC)
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship(back_populates="products", init=False, lazy="noload")
    pricing_tiers: Mapped[list["Pricing"]] = relationship(
        back_populates="product", init=False, cascade="all, delete-orphan", lazy="selectin"
    )


class Pricing(Base, kw_only=True):
    """Pricing tiers for products."""

    __tablename__ = "pricing"

    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True, init=False)
    tenant_id: Mapped[uuid_pkg.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), index=True, nullable=False
    )
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True, nullable=False)

    # Required fields (no defaults) must come first
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    interval: Mapped[str] = mapped_column(String(10), nullable=False)  # monthly, yearly, one_time

    # Optional fields (with defaults) come after
    stripe_price_id: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="usd")
    features: Mapped[list] = mapped_column(JSONB, nullable=False, default_factory=list, server_default="[]")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default_factory=lambda: datetime.now(UTC),
        nullable=False,
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, nullable=True, onupdate=lambda: datetime.now(UTC)
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship(init=False, lazy="noload")
    product: Mapped["Product"] = relationship(back_populates="pricing_tiers", init=False, lazy="noload")


class UserSubscription(Base, kw_only=True):
    """User subscription status and lifecycle tracking."""

    __tablename__ = "user_subscriptions"

    __table_args__: tuple[Index, ...] = (
        Index(
            "idx_subscription_tenant_user_status",
            "tenant_id",
            "user_id",
            "status",
        ),
    )

    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True, init=False)
    tenant_id: Mapped[uuid_pkg.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), index=True, nullable=False
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    subscription_plan_id: Mapped[int] = mapped_column(ForeignKey("subscription_plans.id"), index=True, nullable=False)

    # Required fields (no defaults) must come first
    billing_interval: Mapped[str] = mapped_column(String(10), nullable=False)  # monthly, yearly
    current_period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    current_period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Optional fields (with defaults) come after
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None, index=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active"
    )  # active, canceled, past_due, expired, trialing
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default_factory=lambda: datetime.now(UTC),
        nullable=False,
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, nullable=True, onupdate=lambda: datetime.now(UTC)
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship(init=False, lazy="noload")
    user: Mapped["User"] = relationship(back_populates="user_subscriptions", init=False, lazy="noload")
    subscription_plan: Mapped["SubscriptionPlan"] = relationship(
        back_populates="user_subscriptions", init=False, lazy="noload"
    )
    billing_records: Mapped[list["BillingRecord"]] = relationship(
        back_populates="user_subscription", init=False, cascade="all, delete-orphan", lazy="noload"
    )


class BillingRecord(Base, kw_only=True):
    """Payment transaction history."""

    __tablename__ = "billing_records"

    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True, init=False)
    tenant_id: Mapped[uuid_pkg.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), index=True, nullable=False
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)

    # Required fields (no defaults) must come first
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # succeeded, failed, pending, refunded

    # Optional fields (with defaults) come after
    user_subscription_id: Mapped[int | None] = mapped_column(
        ForeignKey("user_subscriptions.id"), index=True, nullable=True, default=None
    )
    stripe_payment_intent_id: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None, index=True)
    stripe_invoice_id: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None, index=True)
    stripe_refund_id: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None, index=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="usd")
    description: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default_factory=lambda: datetime.now(UTC),
        nullable=False,
        index=True,
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship(init=False, lazy="noload")
    user: Mapped["User"] = relationship(back_populates="billing_records", init=False, lazy="noload")
    user_subscription: Mapped["UserSubscription | None"] = relationship(
        back_populates="billing_records", init=False, lazy="noload"
    )


class WebhookEvent(Base, kw_only=True):
    """Processed webhook events for idempotency.

    Stores the Stripe event IDs that have already been processed to prevent
    duplicate handling of webhook events when Redis is unavailable.

    Scope note: keyed by the provider-global Stripe event id, so this table
    is intentionally tenant-blind — the idempotency domain is the Stripe
    account, not the tenant.
    """

    __tablename__ = "webhook_events"

    event_id: Mapped[str] = mapped_column(String(255), primary_key=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default_factory=lambda: datetime.now(UTC),
        nullable=False,
    )
