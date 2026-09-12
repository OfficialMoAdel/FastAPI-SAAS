"""Layer 8: Enable Row Level Security (RLS) for tenant isolation

Revision ID: 08_enable_rls_policies
Revises: 07_observability
Create Date: 2026-01-30

This migration enables PostgreSQL Row Level Security (RLS) on all multi-tenant
tables to enforce tenant isolation at the database engine level.

CRITICAL: Run performance baseline BEFORE applying this migration!
See: tests/performance/baseline_pre_rls.py
"""

from collections.abc import Sequence

from alembic import op

revision: str = "08_enable_rls_policies"
down_revision: str | None = "07_observability"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Tables that have tenant_id and require RLS
TENANT_TABLES = [
    "post",
    "audit_logs",
    "api_keys",
    "files",
    "feature_flag_overrides",
    "stripe_settings",
    "subscription_plans",
    "products",
    "pricing",
    "user_subscriptions",
    "billing_records",
    "roles",
    "user_roles",
    "storage_settings",
    "webhook_endpoints",
    "webhook_deliveries",
    "notifications",
    "email_template_overrides",
    "ai_chat_sessions",
    "ai_chat_messages",
    "ai_settings",
    "ai_token_usage",
    "token_blacklist",  # P2-3: omitted in original 08, consolidated here
    "password_reset_tokens",
    "email_verification_tokens",
    "user_sso_connections",
    "users",
]


def upgrade() -> None:
    """Enable RLS on all tenant-scoped tables."""
    # 1. Create auth schema for RLS functions
    op.execute("CREATE SCHEMA IF NOT EXISTS auth")

    # 2. Create function to get current tenant ID from session context
    op.execute(
        """
        CREATE OR REPLACE FUNCTION auth.current_tenant_id()
        RETURNS UUID AS $$
        BEGIN
            RETURN NULLIF(current_setting('app.current_tenant_id', true), '')::UUID;
        EXCEPTION WHEN OTHERS THEN
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql STABLE SECURITY DEFINER;
        """
    )

    # 3. Create function to check if RLS should be bypassed
    op.execute(
        """
        CREATE OR REPLACE FUNCTION auth.bypass_rls()
        RETURNS BOOLEAN AS $$
        BEGIN
            RETURN current_setting('app.bypass_rls', true) = 'true';
        EXCEPTION WHEN OTHERS THEN
            RETURN false;
        END;
        $$ LANGUAGE plpgsql STABLE SECURITY DEFINER;
        """
    )

    # 4. Grant execute permissions
    op.execute("GRANT EXECUTE ON FUNCTION auth.current_tenant_id() TO PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION auth.bypass_rls() TO PUBLIC")

    # 5. Enable RLS and create policies for each tenant table
    for table in TENANT_TABLES:
        # Enable RLS
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        # Force RLS so even the table owner is subject to policies
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(f"COMMENT ON TABLE \"{table}\" IS 'RLS enabled for tenant isolation'")

        # Create tenant isolation policy
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON "{table}"
                FOR ALL
                USING (tenant_id = auth.current_tenant_id())
                WITH CHECK (tenant_id = auth.current_tenant_id())
            """
        )

        # Create bypass policy for admin/system operations
        op.execute(
            f"""
            CREATE POLICY bypass_rls ON "{table}"
                FOR ALL
                USING (auth.bypass_rls())
                WITH CHECK (auth.bypass_rls())
            """
        )

        # Add comments to policies
        op.execute(
            f"""
            COMMENT ON POLICY tenant_isolation ON "{table}" IS
            'Enforce tenant isolation - users can only access their tenant data'
            """
        )
        op.execute(
            f"""
            COMMENT ON POLICY bypass_rls ON "{table}" IS
            'Allow bypass for admin operations when app.bypass_rls is set'
            """
        )

    # 6. Create indexes for RLS performance
    # Changed to standard index creation to allow running inside transaction block.
    # CONCURRENTLY is removed as it's not supported in transactions.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_post_tenant_active
        ON post(tenant_id, created_at DESC)
        WHERE is_deleted = false
        """
    )

    # NOTE: no idx_audit_logs_tenant_recent here — audit_logs' hot list is
    # served by idx_audit_logs_tenant_created_id (tenant_id, created_at DESC,
    # id DESC) created in 07_observability; a separate 2-column index would
    # be redundant (former B5 finding).

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_files_tenant_active
        ON files(tenant_id, created_at DESC)
        WHERE is_deleted = false
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_user_tenant_active
        ON "users"(tenant_id, created_at DESC)
        WHERE is_deleted = false
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_tenant_recent
        ON webhook_deliveries(tenant_id, created_at DESC)
        """
    )


def downgrade() -> None:
    """Disable RLS and remove all policies."""

    # 2. Drop indexes created for RLS
    op.execute("DROP INDEX IF EXISTS idx_post_tenant_active")
    op.execute("DROP INDEX IF EXISTS idx_files_tenant_active")
    op.execute("DROP INDEX IF EXISTS idx_user_tenant_active")
    op.execute("DROP INDEX IF EXISTS idx_webhook_deliveries_tenant_recent")

    # 1. Drop policies from all tables
    for table in TENANT_TABLES:
        op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'DROP POLICY IF EXISTS tenant_isolation ON "{table}"')
        op.execute(f'DROP POLICY IF EXISTS bypass_rls ON "{table}"')
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')

    # 3. Drop functions
    op.execute("DROP FUNCTION IF EXISTS auth.current_tenant_id()")
    op.execute("DROP FUNCTION IF EXISTS auth.bypass_rls()")

    # 4. Drop auth schema
    op.execute("DROP SCHEMA IF EXISTS auth CASCADE")
