"""Billing & Payments database models.

Models for Stripe integration, subscription management, products, and billing records.
All models follow row-level multi-tenancy via tenant_id foreign key.
"""

import uuid as uuid_pkg
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, text
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
        # Declared to match 04_billing.py exactly (name, column, unique,
        # predicate). The migration has always created this index, but the ORM
        # did not, so a schema built from ``Base.metadata.create_all`` — which
        # is what tests/conftest.py and scripts/init_test_db.py use — had no
        # unique constraint on ``stripe_subscription_id`` while production
        # does. One Stripe subscription is meant to map to exactly one local
        # row (the ``IntegrityError`` race fallback in
        # billing/webhook/handlers/checkout.py exists to handle that
        # constraint); the partial predicate keeps nullable columns free to
        # repeat NULL, as Postgres UNIQUE semantics require.
        Index(
            "uq_user_subscriptions_stripe_sub",
            "stripe_subscription_id",
            unique=True,
            postgresql_where=text("stripe_subscription_id IS NOT NULL"),
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
    """Webhook event claims for idempotency.

    Stores Stripe event IDs so a delivery is processed at most once. Stripe
    guarantees at-least-once delivery, so this table is what stops a redelivery
    from double-charging a customer.

    S-03 FIX: the row now records WHICH state it is in. Previously the table
    held only ``event_id`` + ``created_at``, so the claim that
    ``_db_idempotency_check`` inserts BEFORE the handler runs was
    indistinguishable from the completion marker written afterwards. A crash
    between the two left a row that ``_is_event_processed`` read as proof of
    completion — and because the row had no expiry, every subsequent Stripe
    retry was acked ``already_processed`` forever. A paid invoice grant could
    be dropped with no error anywhere.

    ``completed_at`` is the discriminator: NULL means "claimed, handler never
    confirmed", non-NULL means "handled".

    CLAIM OWNERSHIP (``claimed_by`` / ``claimed_at``)
    -------------------------------------------------
    ``completed_at`` alone cannot keep two concurrent deliveries apart. A
    reclaiming delivery that sees an unstamped row has no way to tell
    "abandoned by a dead process" from "actively held by a live handler right
    now" — so two deliveries both reclaim and both run the handler, which is
    the double-charge this table exists to prevent.

    ``claimed_by`` is the lock token of the delivery that owns the row and
    ``claimed_at`` is when it took ownership. Together they make the takeover
    a single conditional UPDATE: only a row whose claim is older than
    ``_WEBHOOK_ABANDONED_CLAIM_TTL_SECONDS`` can be stolen, and the theft is
    atomic, so exactly one delivery wins it.

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
    # S-03: NULL = claimed but not completed; non-NULL = the handler ran to
    # success. A NULL row is abandoned ONLY once its `claimed_at` ages past
    # the TTL — a NULL row inside the window belongs to a LIVE handler.
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
        comment="NULL means claimed-but-not-confirmed; set on successful handling",
    )
    # Lock token of the delivery that currently owns this claim. Scopes the
    # release (`DELETE ... WHERE claimed_by = :token`) so a slow, failing
    # delivery can never erase a claim or marker belonging to another.
    claimed_by: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        default=None,
        comment="Lock token of the delivery that owns this claim; NULL when unclaimed/completed",
    )
    # When ownership was taken. The takeover predicate is
    # `claimed_at < now() - abandoned_ttl`, which is what stops a live claim
    # from being stolen out from under a running handler.
    claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
        comment="When the current claim was taken; drives the abandoned-claim TTL",
    )
