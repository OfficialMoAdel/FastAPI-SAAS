# Data Model: Billing & Payments and AI Features Modules

**Branch**: `008-billing-ai-modules` | **Date**: 2026-01-08
**Status**: Design Complete

## Overview

This document defines the database schema for Phase 3 (Billing & Payments) and Phase 5 (AI Features) modules. All tables follow the project's PostgreSQL-only, row-level multi-tenancy patterns.

---

## Entity Relationship Diagram

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                              BILLING & PAYMENTS                                      │
├─────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                      │
│  ┌──────────────┐      ┌───────────────────┐      ┌─────────────────┐              │
│  │   Tenant     │1────*│  StripeSettings   │      │ SubscriptionPlan│              │
│  │              │      │  (1:1 per tenant) │      │                 │              │
│  └──────┬───────┘      └───────────────────┘      └────────┬────────┘              │
│         │                                                   │                       │
│         │1                                                  │1                      │
│         │                                                   │                       │
│         *                                                   *                       │
│  ┌──────┴───────┐      ┌───────────────────┐      ┌────────┴────────┐              │
│  │    User      │1────*│ UserSubscription  │*────1│                 │              │
│  │              │      │                   │      │                 │              │
│  └──────┬───────┘      └───────────────────┘      └─────────────────┘              │
│         │                                                                           │
│         │1                                                                          │
│         *                                                                           │
│  ┌──────┴───────┐                                                                   │
│  │ BillingRecord│                                                                   │
│  │              │                                                                   │
│  └──────────────┘                                                                   │
│                                                                                      │
│  ┌──────────────┐      ┌───────────────────┐                                        │
│  │   Product    │1────*│     Pricing       │                                        │
│  │              │      │                   │                                        │
│  └──────────────┘      └───────────────────┘                                        │
│                                                                                      │
└─────────────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────────────┐
│                                 AI FEATURES                                          │
├─────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                      │
│  ┌──────────────┐      ┌───────────────────┐      ┌─────────────────┐              │
│  │    User      │1────*│  AIChatSession    │1────*│  AIChatMessage  │              │
│  │              │      │                   │      │                 │              │
│  └──────┬───────┘      └───────────────────┘      └─────────────────┘              │
│         │                                                                           │
│         │1                                                                          │
│         │                                                                           │
│         1                                                                           │
│  ┌──────┴───────┐                                                                   │
│  │  AISettings  │                                                                   │
│  │ (1:1 per user)│                                                                  │
│  └──────────────┘                                                                   │
│                                                                                      │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

---

## Billing & Payments Tables

### stripe_settings

Tenant-level Stripe configuration. One record per tenant. API keys are encrypted at rest.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | `BIGINT` | PK, GENERATED ALWAYS AS IDENTITY | Primary key |
| `tenant_id` | `BIGINT` | FK → tenants.id, UNIQUE, NOT NULL | Owning tenant |
| `stripe_customer_id` | `VARCHAR(255)` | NULL | Stripe Customer ID for tenant |
| `stripe_account_id` | `VARCHAR(255)` | NULL | Stripe Connect account ID (future) |
| `encrypted_secret_key` | `TEXT` | NULL | Stripe secret key (Fernet encrypted) |
| `webhook_secret` | `VARCHAR(255)` | NULL | Webhook signing secret (encrypted) |
| `is_active` | `BOOLEAN` | NOT NULL, DEFAULT FALSE | Whether Stripe is configured |
| `created_at` | `TIMESTAMPTZ` | NOT NULL, DEFAULT NOW() | Record creation timestamp |
| `updated_at` | `TIMESTAMPTZ` | NULL | Last update timestamp |

**Indexes**: `tenant_id` (unique)

**Security Note**: `encrypted_secret_key` uses Fernet symmetric encryption with key from `ENCRYPTION_KEY` environment variable. Never expose in API responses or logs.

---

### subscription_plans

Subscription plan definitions with pricing tiers.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | `BIGINT` | PK, GENERATED ALWAYS AS IDENTITY | Primary key |
| `tenant_id` | `BIGINT` | FK → tenants.id, NOT NULL | Owning tenant |
| `name` | `VARCHAR(100)` | NOT NULL | Plan display name |
| `description` | `TEXT` | NULL | Plan description |
| `stripe_price_id_monthly` | `VARCHAR(255)` | NULL | Stripe Price ID for monthly |
| `stripe_price_id_yearly` | `VARCHAR(255)` | NULL | Stripe Price ID for yearly |
| `price_monthly_cents` | `INTEGER` | NOT NULL | Monthly price in cents |
| `price_yearly_cents` | `INTEGER` | NOT NULL | Yearly price in cents |
| `currency` | `VARCHAR(3)` | NOT NULL, DEFAULT 'usd' | ISO 4217 currency code |
| `features` | `JSONB` | NOT NULL, DEFAULT '[]' | List of feature strings |
| `sort_order` | `INTEGER` | NOT NULL, DEFAULT 0 | Display order |
| `is_popular` | `BOOLEAN` | NOT NULL, DEFAULT FALSE | Highlight as popular |
| `is_active` | `BOOLEAN` | NOT NULL, DEFAULT TRUE | Available for new subscriptions |
| `created_at` | `TIMESTAMPTZ` | NOT NULL, DEFAULT NOW() | Record creation timestamp |
| `updated_at` | `TIMESTAMPTZ` | NULL | Last update timestamp |

**Indexes**: `tenant_id`, `(tenant_id, is_active)`

**JSONB Example** (`features`):
```json
["Unlimited projects", "Priority support", "API access", "Custom integrations"]
```

---

### user_subscriptions

User subscription status and lifecycle tracking.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | `BIGINT` | PK, GENERATED ALWAYS AS IDENTITY | Primary key |
| `tenant_id` | `BIGINT` | FK → tenants.id, NOT NULL | Owning tenant |
| `user_id` | `BIGINT` | FK → user.id, NOT NULL | Subscribing user |
| `subscription_plan_id` | `BIGINT` | FK → subscription_plans.id, NOT NULL | Subscribed plan |
| `stripe_subscription_id` | `VARCHAR(255)` | NULL | Stripe Subscription ID |
| `status` | `VARCHAR(20)` | NOT NULL, DEFAULT 'active' | Lifecycle status |
| `billing_interval` | `VARCHAR(10)` | NOT NULL | 'monthly' or 'yearly' |
| `current_period_start` | `TIMESTAMPTZ` | NOT NULL | Billing period start |
| `current_period_end` | `TIMESTAMPTZ` | NOT NULL | Billing period end |
| `cancel_at_period_end` | `BOOLEAN` | NOT NULL, DEFAULT FALSE | Scheduled for cancellation |
| `canceled_at` | `TIMESTAMPTZ` | NULL | When cancellation was requested |
| `created_at` | `TIMESTAMPTZ` | NOT NULL, DEFAULT NOW() | Record creation timestamp |
| `updated_at` | `TIMESTAMPTZ` | NULL | Last update timestamp |

**Status Values**: `active`, `canceled`, `past_due`, `expired`, `trialing`

**Indexes**: `tenant_id`, `user_id`, `(tenant_id, user_id, status)`, `stripe_subscription_id`

**Constraints**: Unique constraint on `(tenant_id, user_id)` for active subscriptions (enforced via application logic to allow historical records)

---

### products

Purchasable products for one-time or add-on purchases.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | `BIGINT` | PK, GENERATED ALWAYS AS IDENTITY | Primary key |
| `tenant_id` | `BIGINT` | FK → tenants.id, NOT NULL | Owning tenant |
| `name` | `VARCHAR(100)` | NOT NULL | Product display name |
| `description` | `TEXT` | NULL | Product description |
| `stripe_product_id` | `VARCHAR(255)` | NULL | Stripe Product ID |
| `is_active` | `BOOLEAN` | NOT NULL, DEFAULT TRUE | Available for purchase |
| `created_at` | `TIMESTAMPTZ` | NOT NULL, DEFAULT NOW() | Record creation timestamp |
| `updated_at` | `TIMESTAMPTZ` | NULL | Last update timestamp |

**Indexes**: `tenant_id`, `(tenant_id, is_active)`

---

### pricing

Pricing tiers for products.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | `BIGINT` | PK, GENERATED ALWAYS AS IDENTITY | Primary key |
| `product_id` | `BIGINT` | FK → products.id, NOT NULL | Parent product |
| `name` | `VARCHAR(100)` | NOT NULL | Pricing tier name |
| `stripe_price_id` | `VARCHAR(255)` | NULL | Stripe Price ID |
| `amount_cents` | `INTEGER` | NOT NULL | Price in cents |
| `currency` | `VARCHAR(3)` | NOT NULL, DEFAULT 'usd' | ISO 4217 currency code |
| `interval` | `VARCHAR(10)` | NOT NULL | 'monthly', 'yearly', 'one_time' |
| `features` | `JSONB` | NOT NULL, DEFAULT '[]' | List of feature strings |
| `is_active` | `BOOLEAN` | NOT NULL, DEFAULT TRUE | Available for purchase |
| `created_at` | `TIMESTAMPTZ` | NOT NULL, DEFAULT NOW() | Record creation timestamp |
| `updated_at` | `TIMESTAMPTZ` | NULL | Last update timestamp |

**Indexes**: `product_id`, `(product_id, is_active)`

---

### billing_records

Payment transaction history.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | `BIGINT` | PK, GENERATED ALWAYS AS IDENTITY | Primary key |
| `tenant_id` | `BIGINT` | FK → tenants.id, NOT NULL | Owning tenant |
| `user_id` | `BIGINT` | FK → user.id, NOT NULL | Paying user |
| `user_subscription_id` | `BIGINT` | FK → user_subscriptions.id, NULL | Related subscription |
| `stripe_payment_intent_id` | `VARCHAR(255)` | NULL | Stripe PaymentIntent ID |
| `stripe_invoice_id` | `VARCHAR(255)` | NULL | Stripe Invoice ID |
| `amount_cents` | `INTEGER` | NOT NULL | Amount in cents |
| `currency` | `VARCHAR(3)` | NOT NULL, DEFAULT 'usd' | ISO 4217 currency code |
| `status` | `VARCHAR(20)` | NOT NULL | Payment status |
| `description` | `VARCHAR(255)` | NULL | Transaction description |
| `created_at` | `TIMESTAMPTZ` | NOT NULL, DEFAULT NOW() | Record creation timestamp |

**Status Values**: `succeeded`, `failed`, `pending`, `refunded`

**Indexes**: `tenant_id`, `user_id`, `(tenant_id, user_id)`, `stripe_payment_intent_id`, `stripe_invoice_id`, `created_at DESC`

---

## AI Features Tables

### ai_chat_sessions

AI conversation containers.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | `BIGINT` | PK, GENERATED ALWAYS AS IDENTITY | Primary key |
| `tenant_id` | `BIGINT` | FK → tenants.id, NOT NULL | Owning tenant |
| `user_id` | `BIGINT` | FK → user.id, NOT NULL | Session owner |
| `title` | `VARCHAR(255)` | NULL | Optional session title |
| `model` | `VARCHAR(50)` | NOT NULL, DEFAULT 'gemma3' | AI model used |
| `created_at` | `TIMESTAMPTZ` | NOT NULL, DEFAULT NOW() | Record creation timestamp |
| `updated_at` | `TIMESTAMPTZ` | NULL | Last activity timestamp |

**Indexes**: `tenant_id`, `user_id`, `(tenant_id, user_id)`, `created_at DESC`

---

### ai_chat_messages

Individual messages within chat sessions.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | `BIGINT` | PK, GENERATED ALWAYS AS IDENTITY | Primary key |
| `session_id` | `BIGINT` | FK → ai_chat_sessions.id, NOT NULL, ON DELETE CASCADE | Parent session |
| `role` | `VARCHAR(10)` | NOT NULL | Message role |
| `content` | `TEXT` | NOT NULL | Message content |
| `token_count` | `INTEGER` | NULL | Token count (AI responses) |
| `created_at` | `TIMESTAMPTZ` | NOT NULL, DEFAULT NOW() | Record creation timestamp |

**Role Values**: `user`, `assistant`, `system`

**Indexes**: `session_id`, `(session_id, created_at)`

**Cascade**: Deleting a session deletes all its messages.

---

### ai_settings

User-level AI preferences. One record per user.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | `BIGINT` | PK, GENERATED ALWAYS AS IDENTITY | Primary key |
| `user_id` | `BIGINT` | FK → user.id, UNIQUE, NOT NULL | Owning user |
| `model` | `VARCHAR(50)` | NOT NULL, DEFAULT 'gemma3' | Preferred model |
| `temperature` | `FLOAT` | NOT NULL, DEFAULT 0.7 | Response randomness (0.0-2.0) |
| `max_tokens` | `INTEGER` | NOT NULL, DEFAULT 2048 | Max response tokens |
| `system_prompt` | `TEXT` | NULL | Custom system prompt |
| `created_at` | `TIMESTAMPTZ` | NOT NULL, DEFAULT NOW() | Record creation timestamp |
| `updated_at` | `TIMESTAMPTZ` | NULL | Last update timestamp |

**Indexes**: `user_id` (unique)

---

## SQLAlchemy Model Examples

### SubscriptionPlan Model

```python
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ...core.db.database import Base

if TYPE_CHECKING:
    from src.app.modules.billing.models import UserSubscription
    from src.app.modules.tenants.models import Tenant


class SubscriptionPlan(Base):
    """Subscription plan definition with pricing tiers."""

    __tablename__ = "subscription_plans"

    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True, init=False)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True, nullable=False)

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)

    stripe_price_id_monthly: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    stripe_price_id_yearly: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)

    price_monthly_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    price_yearly_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="usd")

    features: Mapped[list] = mapped_column(JSONB, nullable=False, default_factory=list, server_default="[]")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_popular: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default_factory=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None, nullable=True)

    # Relationships
    tenant: Mapped["Tenant"] = relationship(back_populates="subscription_plans", init=False)
    user_subscriptions: Mapped[list["UserSubscription"]] = relationship(back_populates="subscription_plan", init=False)
```

### AIChatMessage Model

```python
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ...core.db.database import Base

if TYPE_CHECKING:
    from src.app.modules.ai.models import AIChatSession


class AIChatMessage(Base):
    """Individual message in an AI chat session."""

    __tablename__ = "ai_chat_messages"

    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True, init=False)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("ai_chat_sessions.id", ondelete="CASCADE"),
        index=True,
        nullable=False
    )

    role: Mapped[str] = mapped_column(String(10), nullable=False)  # user, assistant, system
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default_factory=lambda: datetime.now(UTC), nullable=False
    )

    # Relationships
    session: Mapped["AIChatSession"] = relationship(back_populates="messages", init=False)
```

---

## Migration Notes

### New Tables (in order)

1. `stripe_settings` - No dependencies beyond `tenants`
2. `subscription_plans` - Depends on `tenants`
3. `products` - Depends on `tenants`
4. `pricing` - Depends on `products`
5. `user_subscriptions` - Depends on `user`, `subscription_plans`
6. `billing_records` - Depends on `user`, `user_subscriptions`
7. `ai_chat_sessions` - Depends on `user`, `tenants`
8. `ai_chat_messages` - Depends on `ai_chat_sessions`
9. `ai_settings` - Depends on `user`

### Relationship Additions to Existing Models

**Tenant model** - Add relationships:
- `stripe_settings: Mapped["StripeSettings | None"]`
- `subscription_plans: Mapped[list["SubscriptionPlan"]]`
- `products: Mapped[list["Product"]]`
- `ai_chat_sessions: Mapped[list["AIChatSession"]]`

**User model** - Add relationships:
- `user_subscriptions: Mapped[list["UserSubscription"]]`
- `billing_records: Mapped[list["BillingRecord"]]`
- `ai_chat_sessions: Mapped[list["AIChatSession"]]`
- `ai_settings: Mapped["AISettings | None"]`

---

## Validation Rules

| Table | Field | Validation |
|-------|-------|------------|
| `subscription_plans` | `price_monthly_cents` | > 0 |
| `subscription_plans` | `price_yearly_cents` | > 0 |
| `subscription_plans` | `currency` | ISO 4217 lowercase (e.g., 'usd') |
| `pricing` | `amount_cents` | >= 0 |
| `pricing` | `interval` | IN ('monthly', 'yearly', 'one_time') |
| `user_subscriptions` | `status` | IN ('active', 'canceled', 'past_due', 'expired', 'trialing') |
| `user_subscriptions` | `billing_interval` | IN ('monthly', 'yearly') |
| `billing_records` | `status` | IN ('succeeded', 'failed', 'pending', 'refunded') |
| `ai_chat_messages` | `role` | IN ('user', 'assistant', 'system') |
| `ai_settings` | `temperature` | >= 0.0 AND <= 2.0 |
| `ai_settings` | `max_tokens` | > 0 AND <= 32000 |
