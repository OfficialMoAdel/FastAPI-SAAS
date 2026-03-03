"""Layer 3: Access Control - RBAC, API access, and rate limiting

Revision ID: 03_access_control
Revises: 02_authentication
Create Date: 2026-01-30

Tables: permissions, roles, role_permissions, user_roles, api_keys, rate_limit
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "03_access_control"
down_revision: str | None = "02_authentication"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ===== PERMISSIONS =====
    op.create_table(
        "permissions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_permissions")),
    )
    op.create_index("ix_permissions_slug", "permissions", ["slug"], unique=True)

    # Seed default permissions
    op.execute(
        """
        INSERT INTO permissions (slug, description) VALUES
        ('users:read', 'View user profiles'),
        ('users:manage', 'Create, update, delete users'),
        ('roles:read', 'View roles'),
        ('roles:manage', 'Create, update, delete roles'),
        ('roles:assign', 'Assign roles to users'),
        ('posts:read', 'View posts'),
        ('posts:manage', 'Create, update, delete posts'),
        ('files:read', 'View files'),
        ('files:manage', 'Upload, update, delete files'),
        ('billing:read', 'View billing information'),
        ('billing:manage', 'Manage billing and subscriptions'),
        ('settings:read', 'View settings'),
        ('settings:manage', 'Manage settings'),
        ('audit:read', 'View audit logs'),
        ('webhooks:read', 'View webhooks'),
        ('webhooks:manage', 'Create, update, delete webhooks'),
        ('api_keys:read', 'View API keys'),
        ('api_keys:manage', 'Create, revoke API keys')
        """
    )

    # ===== ROLES =====
    op.create_table(
        "roles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_roles")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name=op.f("fk_roles_tenant_id"), ondelete="CASCADE"),
    )
    op.create_index("ix_roles_tenant_id", "roles", ["tenant_id"])
    op.create_unique_constraint("uq_role_tenant_name", "roles", ["tenant_id", "name"])

    # ===== ROLE_PERMISSIONS =====
    op.create_table(
        "role_permissions",
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.Column("permission_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("role_id", "permission_id", name=op.f("pk_role_permissions")),
        sa.ForeignKeyConstraint(
            ["role_id"], ["roles.id"], name=op.f("fk_role_permissions_role_id"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["permission_id"], ["permissions.id"], name=op.f("fk_role_permissions_permission_id"), ondelete="CASCADE"
        ),
    )
    op.create_index("ix_role_permissions_permission_id", "role_permissions", ["permission_id"])

    # ===== USER_ROLES =====
    op.create_table(
        "user_roles",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "tenant_id", "role_id", name=op.f("pk_user_roles")),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], name=op.f("fk_user_roles_user_id"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_user_roles_tenant_id"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], name=op.f("fk_user_roles_role_id"), ondelete="CASCADE"),
    )
    op.create_index("ix_user_roles_tenant_id", "user_roles", ["tenant_id"])
    op.create_index("ix_user_roles_role_id", "user_roles", ["role_id"])
    op.create_index("idx_user_roles_tenant_user", "user_roles", ["tenant_id", "user_id"])
    op.create_index("idx_user_roles_role_tenant", "user_roles", ["role_id", "tenant_id"])

    # ===== API_KEYS =====
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("key_prefix", sa.String(length=10), nullable=False),
        sa.Column("hashed_key", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True, comment="Timestamp when key becomes valid for grace period rotation"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scopes", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_keys")),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], name=op.f("fk_api_keys_user_id"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name=op.f("fk_api_keys_tenant_id"), ondelete="CASCADE"),
    )
    op.create_index("ix_api_keys_user_id", "api_keys", ["user_id"])
    op.create_index("ix_api_keys_tenant_id", "api_keys", ["tenant_id"])
    op.create_index("idx_api_keys_tenant_active", "api_keys", ["tenant_id", "is_active"])
    op.create_index("idx_api_keys_hashed_key", "api_keys", ["hashed_key"], unique=True)
    op.create_index("idx_api_keys_valid_from", "api_keys", ["valid_from"])
    op.create_index("idx_api_keys_tenant_valid", "api_keys", ["tenant_id", "is_active", "valid_from"])

    # ===== RATE_LIMIT =====
    op.create_table(
        "rate_limit",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tier_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("path", sa.String(length=255), nullable=False),
        sa.Column("limit", sa.Integer(), nullable=False),
        sa.Column("period", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rate_limit")),
        sa.ForeignKeyConstraint(["tier_id"], ["tier.id"], name=op.f("fk_rate_limit_tier_id"), ondelete="CASCADE"),
        sa.CheckConstraint('"limit" > 0', name=op.f("ck_rate_limit_limit_positive")),
        sa.CheckConstraint("period > 0", name=op.f("ck_rate_limit_period_positive")),
    )
    op.create_index("ix_rate_limit_tier_path", "rate_limit", ["tier_id", "path"])


def downgrade() -> None:
    op.drop_table("rate_limit")
    op.drop_table("api_keys")
    op.drop_table("user_roles")
    op.drop_table("role_permissions")
    op.drop_table("roles")
    op.drop_table("permissions")
