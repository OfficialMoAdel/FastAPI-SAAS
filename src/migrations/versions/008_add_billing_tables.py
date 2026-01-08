"""Add billing tables for Stripe integration and subscription management

Revision ID: 008_add_billing_tables
Revises: 007_storage_tables
Create Date: 2025-01-08

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "008_add_billing_tables"
down_revision: str | None = "007_storage_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add billing tables: stripe_settings, subscription_plans, products, pricing, user_subscriptions, billing_records."""

    # 1. Create stripe_settings table (1:1 per tenant)
    op.create_table(
        "stripe_settings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("stripe_customer_id", sa.String(length=255), nullable=True),
        sa.Column("stripe_account_id", sa.String(length=255), nullable=True),
        sa.Column("encrypted_secret_key", sa.Text(), nullable=True),
        sa.Column("webhook_secret", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", name="uq_stripe_settings_tenant"),
    )
    op.create_index("idx_stripe_settings_tenant", "stripe_settings", ["tenant_id"], unique=True)

    # 2. Create subscription_plans table
    op.create_table(
        "subscription_plans",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("stripe_price_id_monthly", sa.String(length=255), nullable=True),
        sa.Column("stripe_price_id_yearly", sa.String(length=255), nullable=True),
        sa.Column("price_monthly_cents", sa.Integer(), nullable=False),
        sa.Column("price_yearly_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default=sa.text("'usd'")),
        sa.Column(
            "features",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("is_popular", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("price_monthly_cents > 0", name="check_price_monthly_positive"),
        sa.CheckConstraint("price_yearly_cents > 0", name="check_price_yearly_positive"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_subscription_plans_tenant", "subscription_plans", ["tenant_id"])
    op.create_index("idx_subscription_plans_active", "subscription_plans", ["tenant_id", "is_active"])

    # 3. Create products table
    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("stripe_product_id", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_products_tenant", "products", ["tenant_id"])
    op.create_index("idx_products_active", "products", ["tenant_id", "is_active"])

    # 4. Create pricing table
    op.create_table(
        "pricing",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("stripe_price_id", sa.String(length=255), nullable=True),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default=sa.text("'usd'")),
        sa.Column("interval", sa.String(length=10), nullable=False),
        sa.Column(
            "features",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("amount_cents >= 0", name="check_amount_non_negative"),
        sa.CheckConstraint("interval IN ('monthly', 'yearly', 'one_time')", name="check_pricing_interval"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_pricing_product", "pricing", ["product_id"])
    op.create_index("idx_pricing_active", "pricing", ["product_id", "is_active"])

    # 5. Create user_subscriptions table
    op.create_table(
        "user_subscriptions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("subscription_plan_id", sa.Integer(), nullable=False),
        sa.Column("stripe_subscription_id", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default=sa.text("'active'")),
        sa.Column("billing_interval", sa.String(length=10), nullable=False),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('active', 'canceled', 'past_due', 'expired', 'trialing')",
            name="check_subscription_status",
        ),
        sa.CheckConstraint("billing_interval IN ('monthly', 'yearly')", name="check_billing_interval"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["subscription_plan_id"], ["subscription_plans.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_user_subscriptions_tenant", "user_subscriptions", ["tenant_id"])
    op.create_index("idx_user_subscriptions_user", "user_subscriptions", ["user_id"])
    op.create_index("idx_user_subscriptions_plan", "user_subscriptions", ["subscription_plan_id"])
    op.create_index("idx_user_subscriptions_stripe", "user_subscriptions", ["stripe_subscription_id"])
    op.create_index("idx_user_subscriptions_status", "user_subscriptions", ["tenant_id", "status"])

    # 6. Create billing_records table
    op.create_table(
        "billing_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("user_subscription_id", sa.Integer(), nullable=True),
        sa.Column("stripe_payment_intent_id", sa.String(length=255), nullable=True),
        sa.Column("stripe_invoice_id", sa.String(length=255), nullable=True),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default=sa.text("'usd'")),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.CheckConstraint(
            "status IN ('succeeded', 'failed', 'pending', 'refunded')",
            name="check_billing_record_status",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_subscription_id"], ["user_subscriptions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_billing_records_tenant", "billing_records", ["tenant_id"])
    op.create_index("idx_billing_records_user", "billing_records", ["user_id"])
    op.create_index("idx_billing_records_subscription", "billing_records", ["user_subscription_id"])
    op.create_index("idx_billing_records_stripe_payment", "billing_records", ["stripe_payment_intent_id"])
    op.create_index("idx_billing_records_stripe_invoice", "billing_records", ["stripe_invoice_id"])
    op.create_index("idx_billing_records_created", "billing_records", [sa.text("created_at DESC")])
    op.create_index("idx_billing_records_status", "billing_records", ["tenant_id", "status"])


def downgrade() -> None:
    """Remove billing tables."""
    # Drop billing_records table and indexes
    op.drop_index("idx_billing_records_status", table_name="billing_records")
    op.drop_index("idx_billing_records_created", table_name="billing_records")
    op.drop_index("idx_billing_records_stripe_invoice", table_name="billing_records")
    op.drop_index("idx_billing_records_stripe_payment", table_name="billing_records")
    op.drop_index("idx_billing_records_subscription", table_name="billing_records")
    op.drop_index("idx_billing_records_user", table_name="billing_records")
    op.drop_index("idx_billing_records_tenant", table_name="billing_records")
    op.drop_table("billing_records")

    # Drop user_subscriptions table and indexes
    op.drop_index("idx_user_subscriptions_status", table_name="user_subscriptions")
    op.drop_index("idx_user_subscriptions_stripe", table_name="user_subscriptions")
    op.drop_index("idx_user_subscriptions_plan", table_name="user_subscriptions")
    op.drop_index("idx_user_subscriptions_user", table_name="user_subscriptions")
    op.drop_index("idx_user_subscriptions_tenant", table_name="user_subscriptions")
    op.drop_table("user_subscriptions")

    # Drop pricing table and indexes
    op.drop_index("idx_pricing_active", table_name="pricing")
    op.drop_index("idx_pricing_product", table_name="pricing")
    op.drop_table("pricing")

    # Drop products table and indexes
    op.drop_index("idx_products_active", table_name="products")
    op.drop_index("idx_products_tenant", table_name="products")
    op.drop_table("products")

    # Drop subscription_plans table and indexes
    op.drop_index("idx_subscription_plans_active", table_name="subscription_plans")
    op.drop_index("idx_subscription_plans_tenant", table_name="subscription_plans")
    op.drop_table("subscription_plans")

    # Drop stripe_settings table and indexes
    op.drop_index("idx_stripe_settings_tenant", table_name="stripe_settings")
    op.drop_table("stripe_settings")
