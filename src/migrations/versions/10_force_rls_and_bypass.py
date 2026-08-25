"""force_row_level_security_and_missing_bypass_policies

Revision ID: 10_force_rls_and_bypass
Revises: 09_fix_model_migration_drift
Create Date: 2026-08-25

FORCE ROW LEVEL SECURITY on every tenant-scoped table from 08_enable_rls_policies,
so even the table owner goes through RLS policies.

The six tables that 09_fix_model_migration_drift added RLS to lacked a bypass_rls
policy (unlike the tables handled in 08), which would lock out admin/system
operations once RLS is forced. This adds the same bypass_rls policy used in 08.

Downgrade removes the FORCE clause and drops the newly added bypass policies.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "10_force_rls_and_bypass"
down_revision: str | None = "09_fix_model_migration_drift"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Must mirror TENANT_TABLES in 08_enable_rls_policies.py
TENANT_TABLES = [
    "post",
    "audit_logs",
    "api_keys",
    "files",
    "feature_flag_overrides",
    "stripe_settings",
    "subscription_plans",
    "products",
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
    "users",
]

# Tables given RLS by 09_fix_model_migration_drift without a bypass_rls policy
TABLES_MISSING_BYPASS = [
    "password_reset_tokens",
    "email_verification_tokens",
    "ai_chat_messages",
    "ai_settings",
    "user_sso_connections",
    "ai_token_usage",
]


def upgrade() -> None:
    """Force RLS on all tenant tables and add missing bypass policies."""
    # 1. Force RLS so table owners are subject to policies too
    for table in TENANT_TABLES:
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')

    # 2. Add bypass_rls policy to tables that 09 gave RLS but no bypass path
    for table in TABLES_MISSING_BYPASS:
        op.execute(
            f"""
            CREATE POLICY bypass_rls ON "{table}"
                FOR ALL
                USING (auth.bypass_rls())
                WITH CHECK (auth.bypass_rls())
            """
        )
        op.execute(
            f"""
            COMMENT ON POLICY bypass_rls ON "{table}" IS
            'Allow bypass for admin operations when app.bypass_rls is set'
            """
        )


def downgrade() -> None:
    """Remove FORCE from tenant tables and drop added bypass policies."""
    for table in TABLES_MISSING_BYPASS:
        op.execute(f'DROP POLICY IF EXISTS bypass_rls ON "{table}"')

    for table in TENANT_TABLES:
        op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
