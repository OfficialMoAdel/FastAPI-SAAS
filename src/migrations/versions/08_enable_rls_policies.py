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
    "passkey_credentials",  # H1: credential tables carry tenant_id (02) — RLS enforced
    "magic_links",
    "mfa_methods",
    "users",
]

# S-11: Tables with nullable `tenant_id`.
#
# A NULL `tenant_id` represents a global or system row. The strict predicate
# `tenant_id = auth.current_tenant_id()` evaluates to false for NULL, hiding
# these rows and blocking their insertion without a bypass.
#
# These are the only two tables in TENANT_TABLES with a nullable `tenant_id`.
# All other tables use the strict predicate, preserving tenant isolation.
GLOBAL_TENANT_TABLES = frozenset({"notifications", "ai_token_usage"})


def _tenant_read_predicate(table: str) -> str:
    """RLS `USING` predicate for ``table`` — what a tenant may SEE.

    Strict equality for tenant-scoped tables. For the handful whose
    ``tenant_id`` is legitimately nullable, admit the global (NULL) rows: a
    strict predicate evaluates to NULL on them and SQL treats NULL as
    not-true, so they would be invisible to everyone.
    """
    if table in GLOBAL_TENANT_TABLES:
        return "(tenant_id IS NULL OR tenant_id = auth.current_tenant_id())"
    return "tenant_id = auth.current_tenant_id()"


def _tenant_write_predicate(table: str) -> str:
    """RLS `WITH CHECK` predicate for ``table`` — what a tenant may WRITE.

    Always the strict form, INCLUDING for the nullable-tenant tables.

    This is deliberately NOT the same as the read predicate. `USING` decides
    visibility; `WITH CHECK` decides authorship. Reusing one relaxed string for
    both granted a WRITE privilege alongside the read access: any tenant-bound
    role could INSERT a row with ``tenant_id IS NULL``, and `USING` then made
    that row visible to EVERY tenant — a way to forge a platform-wide
    notification, or poison the shared AI token-usage ledger, from inside a
    single tenant.

    Global rows are still creatable: `bypass_rls` is the audited path for
    system writers, and that is where they belong.
    """
    return "tenant_id = auth.current_tenant_id()"


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
                USING ({_tenant_read_predicate(table)})
                WITH CHECK ({_tenant_write_predicate(table)})
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
