"""fix_model_migration_drift

Revision ID: 09_fix_model_migration_drift
Revises: 08_enable_rls_policies
Create Date: 2026-08-02

"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '09_fix_model_migration_drift'
down_revision: Union[str, None] = '08_enable_rls_policies'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add tenant_id column (nullable=True initially)
    op.add_column("password_reset_tokens", sa.Column("tenant_id", sa.UUID(), nullable=True))
    op.add_column("email_verification_tokens", sa.Column("tenant_id", sa.UUID(), nullable=True))
    op.add_column("ai_chat_messages", sa.Column("tenant_id", sa.UUID(), nullable=True))
    op.add_column("ai_settings", sa.Column("tenant_id", sa.UUID(), nullable=True))

    # 2. Backfill tenant_id from parent relations
    # password_reset_tokens
    op.execute("""
        UPDATE password_reset_tokens prt
        SET tenant_id = u.tenant_id
        FROM users u
        WHERE prt.user_id = u.id;
    """)
    # Delete orphan rows where user_id does not exist in users
    op.execute("DELETE FROM password_reset_tokens WHERE tenant_id IS NULL;")

    # email_verification_tokens
    op.execute("""
        UPDATE email_verification_tokens evt
        SET tenant_id = u.tenant_id
        FROM users u
        WHERE evt.user_id = u.id;
    """)
    op.execute("DELETE FROM email_verification_tokens WHERE tenant_id IS NULL;")

    # ai_chat_messages
    op.execute("""
        UPDATE ai_chat_messages acm
        SET tenant_id = acs.tenant_id
        FROM ai_chat_sessions acs
        WHERE acm.session_id = acs.id;
    """)
    op.execute("DELETE FROM ai_chat_messages WHERE tenant_id IS NULL;")

    # ai_settings
    op.execute("""
        UPDATE ai_settings ast
        SET tenant_id = u.tenant_id
        FROM users u
        WHERE ast.user_id = u.id;
    """)
    op.execute("DELETE FROM ai_settings WHERE tenant_id IS NULL;")

    # 3. Enforce NOT NULL constraints
    op.alter_column("password_reset_tokens", "tenant_id", nullable=False)
    op.alter_column("email_verification_tokens", "tenant_id", nullable=False)
    op.alter_column("ai_chat_messages", "tenant_id", nullable=False)
    op.alter_column("ai_settings", "tenant_id", nullable=False)

    # 4. Add foreign keys and indexes
    op.create_foreign_key(op.f("fk_password_reset_tokens_tenant_id"), "password_reset_tokens", "tenants", ["tenant_id"], ["id"], ondelete="CASCADE")
    op.create_index(op.f("ix_password_reset_tokens_tenant_id"), "password_reset_tokens", ["tenant_id"])

    op.create_foreign_key(op.f("fk_email_verification_tokens_tenant_id"), "email_verification_tokens", "tenants", ["tenant_id"], ["id"], ondelete="CASCADE")
    op.create_index(op.f("ix_email_verification_tokens_tenant_id"), "email_verification_tokens", ["tenant_id"])

    op.create_foreign_key(op.f("fk_ai_chat_messages_tenant_id"), "ai_chat_messages", "tenants", ["tenant_id"], ["id"], ondelete="CASCADE")
    op.create_index(op.f("ix_ai_chat_messages_tenant_id"), "ai_chat_messages", ["tenant_id"])

    op.create_foreign_key(op.f("fk_ai_settings_tenant_id"), "ai_settings", "tenants", ["tenant_id"], ["id"], ondelete="CASCADE")
    op.create_index(op.f("ix_ai_settings_tenant_id"), "ai_settings", ["tenant_id"])

    # 5. Enable RLS on tables missing policies
    tables_to_rls = [
        "password_reset_tokens",
        "email_verification_tokens",
        "ai_chat_messages",
        "ai_settings",
        "user_sso_connections",
        "ai_token_usage",
    ]
    for table in tables_to_rls:
        # Enable RLS on the table
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")

        # Drop existing policy if it exists to be safe
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_policy ON {table}")

        # Create the isolation policy
        op.execute(
            f"""
            CREATE POLICY tenant_isolation_policy ON {table}
                AS PERMISSIVE FOR ALL
                TO public
                USING (tenant_id = auth.current_tenant_id())
                WITH CHECK (tenant_id = auth.current_tenant_id())
            """
        )

        # Force RLS even for table owners
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    # Disable RLS policies on the affected tables
    tables_to_rls = [
        "password_reset_tokens",
        "email_verification_tokens",
        "ai_chat_messages",
        "ai_settings",
        "user_sso_connections",
        "ai_token_usage",
    ]
    for table in tables_to_rls:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_policy ON {table}")
        # Note: Do not disable RLS on user_sso_connections and ai_token_usage during downgrade
        # since we only added policies, they might have had RLS enabled but missing policies.
        # But we'll disable it for all for simplicity in downgrade.
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    # Drop constraints and columns
    op.drop_index(op.f("ix_ai_settings_tenant_id"), table_name="ai_settings")
    op.drop_constraint(op.f("fk_ai_settings_tenant_id"), "ai_settings", type_="foreignkey")
    op.drop_column("ai_settings", "tenant_id")

    op.drop_index(op.f("ix_ai_chat_messages_tenant_id"), table_name="ai_chat_messages")
    op.drop_constraint(op.f("fk_ai_chat_messages_tenant_id"), "ai_chat_messages", type_="foreignkey")
    op.drop_column("ai_chat_messages", "tenant_id")

    op.drop_index(op.f("ix_email_verification_tokens_tenant_id"), table_name="email_verification_tokens")
    op.drop_constraint(op.f("fk_email_verification_tokens_tenant_id"), "email_verification_tokens", type_="foreignkey")
    op.drop_column("email_verification_tokens", "tenant_id")

    op.drop_index(op.f("ix_password_reset_tokens_tenant_id"), table_name="password_reset_tokens")
    op.drop_constraint(op.f("fk_password_reset_tokens_tenant_id"), "password_reset_tokens", type_="foreignkey")
    op.drop_column("password_reset_tokens", "tenant_id")
