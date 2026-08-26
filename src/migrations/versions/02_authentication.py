"""Layer 2: Authentication - All authentication mechanisms and security tokens

Revision ID: 02_authentication
Revises: 01_foundation
Create Date: 2026-01-30

Tables: token_blacklist, passkey_credentials, magic_links, mfa_methods, password_reset_tokens,
        email_verification_tokens, sso_settings, user_sso_connections
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "02_authentication"
down_revision: str | None = "01_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ===== TOKEN_BLACKLIST =====
    op.create_table(
        "token_blacklist",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("token", sa.String(length=500), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_token_blacklist_tenant_id_tenants"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_token_blacklist")),
    )
    op.create_index("ix_token_blacklist_token", "token_blacklist", ["token"], unique=True)
    op.create_index("idx_token_blacklist_active", "token_blacklist", ["expires_at"])
    op.create_index("ix_token_blacklist_tenant_id", "token_blacklist", ["tenant_id"])
    op.create_index("ix_token_blacklist_tenant_expires", "token_blacklist", ["tenant_id", "expires_at"])

    # ===== PASSKEY_CREDENTIALS =====
    op.create_table(
        "passkey_credentials",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("credential_id", sa.LargeBinary(), nullable=False),
        sa.Column("public_key", sa.LargeBinary(), nullable=False),
        sa.Column("credential_id_hash", sa.String(length=64), nullable=False),
        sa.Column("counter", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("device_type", sa.String(length=50), nullable=True),
        sa.Column("device_name", sa.String(length=100), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_passkey_credentials")),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_passkey_credentials_user_id"), ondelete="CASCADE"
        ),
    )
    op.create_index("idx_passkey_credentials_user_id", "passkey_credentials", ["user_id"])
    op.create_index("idx_passkey_credential_id_hash", "passkey_credentials", ["credential_id_hash"], unique=True)

    # ===== MAGIC_LINKS =====
    op.create_table(
        "magic_links",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token", sa.String(length=255), nullable=False),
        sa.Column("action", sa.String(length=50), nullable=False, server_default=sa.text("'login'")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_magic_links")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_magic_links_user_id"), ondelete="CASCADE"),
    )
    op.create_index("idx_magic_links_token", "magic_links", ["token"], unique=True)
    op.create_index("idx_magic_links_user_id", "magic_links", ["user_id"])
    op.create_index("idx_magic_links_expires_at", "magic_links", ["expires_at"])

    # ===== MFA_METHODS =====
    op.create_table(
        "mfa_methods",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("secret", sa.String(length=255), nullable=False),
        sa.Column(
            "backup_codes", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'"), nullable=True
        ),
        sa.Column("is_primary", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_mfa_methods_user_id"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mfa_methods")),
        sa.UniqueConstraint("user_id", "type", name=op.f("uq_user_mfa_type")),
    )
    op.create_index("ix_mfa_methods_user_id", "mfa_methods", ["user_id"])
    op.create_index("ix_mfa_methods_type", "mfa_methods", ["type"])

    # ===== PASSWORD_RESET_TOKENS =====
    op.create_table(
        "password_reset_tokens",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("token", sa.String(length=255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_password_reset_tokens")),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_password_reset_tokens_user_id"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_password_reset_tokens_tenant_id"), ondelete="CASCADE"
        ),
    )
    op.create_index("ix_password_reset_tokens_token", "password_reset_tokens", ["token"], unique=True)
    op.create_index("ix_password_reset_tokens_user_id", "password_reset_tokens", ["user_id"])
    op.create_index(op.f("ix_password_reset_tokens_tenant_id"), "password_reset_tokens", ["tenant_id"])

    # ===== EMAIL_VERIFICATION_TOKENS =====
    op.create_table(
        "email_verification_tokens",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("token", sa.String(length=255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_email_verification_tokens")),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_email_verification_tokens_user_id"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_email_verification_tokens_tenant_id"), ondelete="CASCADE"
        ),
    )
    op.create_index("ix_email_verification_tokens_token", "email_verification_tokens", ["token"], unique=True)
    op.create_index(op.f("ix_email_verification_tokens_tenant_id"), "email_verification_tokens", ["tenant_id"])

    # ===== SSO_SETTINGS (host-level, NO tenant_id) =====
    op.create_table(
        "sso_settings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("client_id", sa.LargeBinary(), nullable=True),
        sa.Column("client_secret", sa.LargeBinary(), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("additional_config", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sso_settings")),
    )
    op.create_index("idx_sso_settings_provider", "sso_settings", ["provider"], unique=True)

    # ===== USER_SSO_CONNECTIONS =====
    op.create_table(
        "user_sso_connections",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("provider_user_id", sa.String(length=255), nullable=False),
        sa.Column("access_token", sa.LargeBinary(), nullable=True),
        sa.Column("refresh_token", sa.LargeBinary(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("profile_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_sso_connections")),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_user_sso_connections_user_id"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_user_sso_connections_tenant_id"), ondelete="CASCADE"
        ),
    )
    op.create_index("idx_user_sso_connections_user_id", "user_sso_connections", ["user_id"])
    op.create_unique_constraint("uq_user_sso_user_provider", "user_sso_connections", ["user_id", "provider"])
    op.create_unique_constraint("uq_user_sso_provider_user", "user_sso_connections", ["provider", "provider_user_id"])
    op.create_index("idx_user_sso_lookup", "user_sso_connections", ["provider", "provider_user_id"])


def downgrade() -> None:
    op.drop_table("user_sso_connections")
    op.drop_table("sso_settings")
    op.drop_table("email_verification_tokens")
    op.drop_table("password_reset_tokens")
    op.drop_table("mfa_methods")
    op.drop_table("magic_links")
    op.drop_table("passkey_credentials")
    op.drop_table("token_blacklist")
