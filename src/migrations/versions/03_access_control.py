"""Layer 3: Access Control - RBAC, API access, and rate limiting

Revision ID: 03_access_control
Revises: 02_authentication
Create Date: 2026-01-30

Tables: permissions, roles, role_permissions, user_roles, api_keys, rate_limit
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

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

    # Seed the permission catalog.
    #
    # Run-3 finding rbac.seeded_permission_catalog_drifted_from_enum: this block
    # used to seed a hardcoded list of 18 slugs that had drifted from
    # src/app/core/rbac/permissions.py (then 24 values, now 25). Fifteen enum
    # permissions were therefore UNGRANTABLE, because add_permission_to_role
    # resolves a slug against this table and raises ModelNotFoundError when it
    # is absent. Adding a permission to the enum alone silently produced an
    # inert authorization gate.
    #
    # The enum is the single source of truth, so the catalog is derived from it
    # at migration time rather than re-typed here. A hand-maintained copy of a
    # growing enum is exactly what caused the drift.
    #
    # Because roles/role_permissions are created EMPTY below and no role is
    # seeded, this rebuild is what makes every declared permission grantable
    # from a clean database. Operators grant them per role afterwards.
    from src.app.core.rbac.permissions import Permission as _PermissionEnum

    op.get_bind().execute(
        sa.text(
            "INSERT INTO permissions (slug, description) VALUES (:slug, :description) ON CONFLICT (slug) DO NOTHING"
        ),
        [{"slug": p.value, "description": p.name.replace("_", " ").title()} for p in _PermissionEnum],
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_user_roles_user_id"), ondelete="CASCADE"),
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
        # api_keys.key_prefix is String(8) on the ORM
        # (api_keys/models.py, KEY_PREFIX_LENGTH = 8). This layer said 10, so a
        # migrated database had a wider column than the code that fills it. Every
        # generated prefix fits both, so nothing has broken; the fix is that the
        # schema stops claiming something the model does not (FastAPI_saas-vthy).
        sa.Column("key_prefix", sa.String(length=8), nullable=False),
        sa.Column("hashed_key", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "valid_from",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Timestamp when key becomes valid for grace period rotation",
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        # api_keys.scopes is Text on the ORM (api_keys/models.py), and the whole
        # read/write path is string-shaped to match: create_api_key stores
        # json_dumps(...) AS A STRING, rotate re-binds whatever came back
        # verbatim, and validate_api_key reads it with json_loads. This layer
        # declared JSONB instead (FastAPI_saas-vthy), and on a database built by
        # `alembic upgrade head` that broke API-key AUTHENTICATION:
        # SQLAlchemy's asyncpg dialect decodes a jsonb column to a Python list,
        # so `json_loads(key.scopes)` (service.py:652) raises JSONDecodeError on
        # a list, and _validate_from_db has no handler for it - every
        # X-API-Key request fails with 500. MEASURED on a scratch database built
        # by `alembic upgrade 03_access_control`, with the create_all database
        # as the control: the INSERT succeeds either way (asyncpg sends untyped
        # parameters and the server coerces the literal), but the ORM read
        # returns `list` on jsonb and `str` on Text, and only Text keeps
        # json_loads working. Nothing in the application queries scopes as JSON,
        # so jsonb bought nothing and cost the read path.
        sa.Column("scopes", sa.Text(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_keys")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_api_keys_user_id"), ondelete="CASCADE"),
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
        # The (tier_id, path) lookup is the hot path of this table, and the
        # ORM declares the SAME columns as a UNIQUE constraint
        # (``uq_rate_limit_tier_path`` in
        # ``src/app/modules/rate_limits/models.py``). A unique constraint
        # builds its own index, so the separate plain index this layer used
        # to create (``ix_rate_limit_tier_path``) was a second structure over
        # identical columns — pure write and storage cost — AND it left the
        # two layers disagreeing: the ORM promised uniqueness the schema did
        # not have. Creating the constraint instead gives one index, the
        # uniqueness the ORM declares, and agreement between the layers.
        sa.UniqueConstraint("tier_id", "path", name="uq_rate_limit_tier_path"),
    )


def downgrade() -> None:
    op.drop_table("rate_limit")
    op.drop_table("api_keys")
    op.drop_table("user_roles")
    op.drop_table("role_permissions")
    op.drop_table("roles")
    op.execute(
        """
        DELETE FROM permissions WHERE slug IN (
            'users:read', 'users:manage', 'roles:read', 'roles:manage', 'roles:assign',
            'posts:read', 'posts:manage', 'files:read', 'files:manage', 'billing:read',
            'billing:manage', 'settings:read', 'settings:manage', 'audit:read',
            'webhooks:read', 'webhooks:manage', 'api_keys:read', 'api_keys:manage'
        )
        """
    )
    op.drop_table("permissions")
