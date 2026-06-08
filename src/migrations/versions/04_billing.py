"""Layer 4: Billing - Stripe integration and subscription management

Revision ID: 04_billing
Revises: 03_access_control
Create Date: 2026-01-30

Tables: stripe_settings, subscription_plans, products, pricing, user_subscriptions, billing_records
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "04_billing"
down_revision: str | None = "03_access_control"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ===== STRIPE_SETTINGS =====
    op.create_table(
        "stripe_settings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("stripe_customer_id", sa.String(length=255), nullable=True),
        sa.Column("stripe_account_id", sa.String(length=255), nullable=True),
        sa.Column("encrypted_secret_key", sa.Text(), nullable=True),
        sa.Column("webhook_secret", sa.LargeBinary(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stripe_settings")),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_stripe_settings_tenant_id"), ondelete="CASCADE"
        ),
    )
    op.create_index("idx_stripe_settings_tenant", "stripe_settings", ["tenant_id"], unique=True)

    # ===== SUBSCRIPTION_PLANS =====
    op.create_table(
        "subscription_plans",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("price_monthly_cents", sa.Integer(), nullable=False),
        sa.Column("price_yearly_cents", sa.Integer(), nullable=False),
        sa.Column("stripe_price_id_monthly", sa.String(length=255), nullable=True),
        sa.Column("stripe_price_id_yearly", sa.String(length=255), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default=sa.text("'usd'")),
        sa.Column("features", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("is_popular", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_subscription_plans")),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_subscription_plans_tenant_id"), ondelete="CASCADE"
        ),
        sa.CheckConstraint("price_monthly_cents > 0", name=op.f("ck_subscription_plans_price_monthly_positive")),
        sa.CheckConstraint("price_yearly_cents > 0", name=op.f("ck_subscription_plans_price_yearly_positive")),
    )
    op.create_index("idx_subscription_plans_tenant", "subscription_plans", ["tenant_id"])
    op.create_index("idx_subscription_plans_active", "subscription_plans", ["tenant_id", "is_active"])

    # ===== PRODUCTS =====
    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("stripe_product_id", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_products")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name=op.f("fk_products_tenant_id"), ondelete="CASCADE"),
    )
    op.create_index("idx_products_tenant", "products", ["tenant_id"])
    op.create_index("idx_products_active", "products", ["tenant_id", "is_active"])

    # ===== PRICING =====
    op.create_table(
        "pricing",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("interval", sa.String(length=20), nullable=False),
        sa.Column("stripe_price_id", sa.String(length=255), nullable=True),
        sa.Column("features", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pricing")),
        sa.ForeignKeyConstraint(
            ["product_id"], ["products.id"], name=op.f("fk_pricing_product_id"), ondelete="CASCADE"
        ),
        sa.CheckConstraint("amount_cents >= 0", name=op.f("ck_pricing_amount_non_negative")),
        sa.CheckConstraint("interval IN ('monthly', 'yearly', 'one_time')", name=op.f("ck_pricing_interval_valid")),
    )
    op.create_index("idx_pricing_product", "pricing", ["product_id"])
    op.create_index("idx_pricing_active", "pricing", ["product_id", "is_active"])

    # ===== USER_SUBSCRIPTIONS =====
    op.create_table(
        "user_subscriptions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("subscription_plan_id", sa.Integer(), nullable=False),
        sa.Column("stripe_subscription_id", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("billing_interval", sa.String(length=20), nullable=False),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_subscriptions")),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_user_subscriptions_tenant_id"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_user_subscriptions_user_id"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["subscription_plan_id"],
            ["subscription_plans.id"],
            name=op.f("fk_user_subscriptions_plan_id"),
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'canceled', 'past_due', 'expired', 'trialing')",
            name=op.f("ck_user_subscriptions_status_valid"),
        ),
    )
    op.create_index("idx_user_subscriptions_tenant", "user_subscriptions", ["tenant_id"])
    op.create_index("idx_user_subscriptions_user", "user_subscriptions", ["user_id"])
    op.create_index("idx_subscription_tenant_user_status", "user_subscriptions", ["tenant_id", "user_id", "status"])

    # ===== BILLING_RECORDS =====
    op.create_table(
        "billing_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("user_subscription_id", sa.Integer(), nullable=True),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default=sa.text("'usd'")),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("stripe_payment_intent_id", sa.String(length=255), nullable=True),
        sa.Column("stripe_invoice_id", sa.String(length=255), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_billing_records")),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_billing_records_tenant_id"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_billing_records_user_id"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["user_subscription_id"],
            ["user_subscriptions.id"],
            name=op.f("fk_billing_records_subscription_id"),
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "status IN ('succeeded', 'failed', 'pending', 'refunded')", name=op.f("ck_billing_records_status_valid")
        ),
    )
    op.create_index("idx_billing_records_tenant", "billing_records", ["tenant_id"])
    op.create_index("idx_billing_records_user", "billing_records", ["user_id"])
    op.create_index("idx_billing_records_created", "billing_records", ["created_at"], postgresql_using="brin")

    # ===== WEBHOOK_EVENTS =====
    op.create_table(
        "webhook_events",
        sa.Column("event_id", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("event_id", name=op.f("pk_webhook_events")),
    )
    op.create_index("idx_webhook_events_event", "webhook_events", ["event_id"])


def downgrade() -> None:
    op.drop_table("webhook_events")
    op.drop_table("billing_records")
    op.drop_table("user_subscriptions")
    op.drop_table("pricing")
    op.drop_table("products")
    op.drop_table("subscription_plans")
    op.drop_table("stripe_settings")
