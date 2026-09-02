"""Layer 1: Foundation - Core entities that everything else depends on

Revision ID: 01_foundation
Revises: None
Create Date: 2026-01-30

Tables: tenants, tier, user, post
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "01_foundation"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ===== TENANTS =====
    op.create_table(
        "tenants",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("feature_flags", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("storage_usage_bytes", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("file_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenants")),
    )
    op.create_index("ix_tenants_slug", "tenants", ["slug"], unique=True)
    op.create_index("idx_tenants_slug_lower", "tenants", [sa.text("lower(slug)")])

    # Seed system tenant (id: 00000000-0000-0000-0000-000000000000) for system-level audit logs and RLS bypass
    op.execute(
        """
        INSERT INTO tenants (id, name, slug, is_active, created_at, updated_at)
        VALUES (
            '00000000-0000-0000-0000-000000000000'::UUID,
            'System',
            'system',
            true,
            now(),
            now()
        )
        ON CONFLICT (id) DO NOTHING;
        """
    )
    # ===== TIER =====
    op.create_table(
        "tier",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tier")),
        sa.UniqueConstraint("name", name=op.f("uq_tier_name")),
    )

    # ===== USER =====
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("tier_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("username", sa.String(length=50), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("uuid", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("profile_image_url", sa.String(length=500), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_superuser", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_email_verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name=op.f("fk_user_tenant_id"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tier_id"], ["tier.id"], name=op.f("fk_user_tier_id"), ondelete="SET NULL"),
    )
    # Tenant-scoped PARTIAL unique indexes, exactly as declared in
    # users/models.py __table_args__. Partial (WHERE is_deleted = false) so a
    # soft-deleted row does not block re-registration of the same
    # email/username within a tenant. (Previously this file created a global
    # UNIQUE ix_user_username and a non-unique ix_user_email, which drifted
    # from the ORM and broke IntegrityError-name matching; the fix was folded
    # in here during development instead of a separate migration.)
    op.create_index(
        "uq_user_email_tenant",
        "users",
        ["email", "tenant_id"],
        unique=True,
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index(
        "uq_user_username_tenant",
        "users",
        ["username", "tenant_id"],
        unique=True,
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index("ix_user_uuid", "users", ["uuid"], unique=True)
    op.create_index("idx_users_tenant_email", "users", ["tenant_id", "email"])
    op.create_index("idx_users_tenant_username", "users", ["tenant_id", "username"])
    op.create_index("idx_users_tenant_deleted", "users", ["tenant_id", "is_deleted"])
    # Functional indexes for case-insensitive lookups (auth queries use lower(col) = param)
    op.create_index("idx_users_tenant_lower_email", "users", ["tenant_id", sa.text("lower(email)")])
    op.create_index("idx_users_lower_username", "users", [sa.text("lower(username)")])
    op.create_index("ix_user_is_active", "users", ["is_active"])
    op.create_index("ix_user_token_version", "users", ["token_version"])

    # ===== POST =====
    op.create_table(
        "post",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("uuid", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("media_url", sa.String(length=500), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_post")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name=op.f("fk_post_tenant_id"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], name=op.f("fk_post_created_by_user_id"), ondelete="CASCADE"
        ),
    )
    op.create_index("ix_post_tenant_id", "post", ["tenant_id"])
    op.create_index("ix_post_uuid", "post", ["uuid"], unique=True)
    op.create_index("idx_posts_tenant_user_deleted", "post", ["tenant_id", "created_by_user_id", "is_deleted"])


def downgrade() -> None:
    op.drop_table("post")
    op.drop_table("users")
    op.drop_table("tier")
    op.drop_table("tenants")
