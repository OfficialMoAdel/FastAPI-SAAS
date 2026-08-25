"""pricing_tenant_isolation_and_stripe_settings_encrypted_secret

Revision ID: 12_pricing_rls_stripe_secret
Revises: 11_stripe_partial_unique
Create Date: 2026-08-25

1. pricing: add tenant_id (backfilled from products), FK to tenants, index,
   and full RLS treatment (ENABLE + FORCE + tenant_isolation + bypass_rls).
   pricing was missing from TENANT_TABLES in 08_enable_rls_policies.

2. stripe_settings: migration 04 created a binary `webhook_secret` column that
   is not part of the model; the model expects TEXT `encrypted_webhook_secret`.
   This adds encrypted_webhook_secret and drops the legacy binary webhook_secret.
   DATA LOSS NOTE: legacy webhook secrets stored in the binary column are lost;
   tenants must re-enter their Stripe webhook secret after deploy.

Downgrade reverses both changes.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "12_pricing_rls_stripe_secret"
down_revision: str | None = "11_stripe_partial_unique"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ===== PRICING: tenant_id + RLS =====
    # 1. Add tenant_id nullable first (cannot NOT NULL without backfill)
    op.add_column("pricing", sa.Column("tenant_id", sa.UUID(), nullable=True))

    # 2. Backfill from parent product
    op.execute("""
        UPDATE pricing SET tenant_id = products.tenant_id
        FROM products WHERE pricing.product_id = products.id;
    """)
    # Orphan rows whose product vanished cannot be attributed to a tenant
    op.execute("DELETE FROM pricing WHERE tenant_id IS NULL;")

    # 3. Enforce NOT NULL, add FK and index
    op.alter_column("pricing", "tenant_id", nullable=False)
    op.create_foreign_key(
        op.f("fk_pricing_tenant_id"), "pricing", "tenants", ["tenant_id"], ["id"], ondelete="CASCADE"
    )
    op.create_index("idx_pricing_tenant", "pricing", ["tenant_id"])

    # 4. RLS: enable, force, isolation policy, bypass policy
    op.execute('ALTER TABLE "pricing" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "pricing" FORCE ROW LEVEL SECURITY')
    op.execute(
        """
        CREATE POLICY tenant_isolation ON "pricing"
            FOR ALL
            USING (tenant_id = auth.current_tenant_id())
            WITH CHECK (tenant_id = auth.current_tenant_id())
        """
    )
    op.execute(
        """
        COMMENT ON POLICY tenant_isolation ON "pricing" IS
        'Enforce tenant isolation - users can only access their tenant data'
        """
    )
    op.execute(
        """
        CREATE POLICY bypass_rls ON "pricing"
            FOR ALL
            USING (auth.bypass_rls())
            WITH CHECK (auth.bypass_rls())
        """
    )
    op.execute(
        """
        COMMENT ON POLICY bypass_rls ON "pricing" IS
        'Allow bypass for admin operations when app.bypass_rls is set'
        """
    )

    # ===== STRIPE_SETTINGS: encrypted_webhook_secret swap =====
    op.add_column("stripe_settings", sa.Column("encrypted_webhook_secret", sa.Text(), nullable=True))
    # Legacy binary column is not in the model; dropping loses stored secrets.
    # Tenants must re-set their webhook secret after deploy.
    op.drop_column("stripe_settings", "webhook_secret")


def downgrade() -> None:
    # ===== STRIPE_SETTINGS: restore legacy binary column =====
    op.add_column("stripe_settings", sa.Column("webhook_secret", sa.LargeBinary(), nullable=True))
    op.drop_column("stripe_settings", "encrypted_webhook_secret")

    # ===== PRICING: remove RLS and tenant_id =====
    op.execute('DROP POLICY IF EXISTS bypass_rls ON "pricing"')
    op.execute('DROP POLICY IF EXISTS tenant_isolation ON "pricing"')
    op.execute('ALTER TABLE "pricing" NO FORCE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "pricing" DISABLE ROW LEVEL SECURITY')

    op.drop_index("idx_pricing_tenant", table_name="pricing")
    op.drop_constraint(op.f("fk_pricing_tenant_id"), "pricing", type_="foreignkey")
    op.drop_column("pricing", "tenant_id")
