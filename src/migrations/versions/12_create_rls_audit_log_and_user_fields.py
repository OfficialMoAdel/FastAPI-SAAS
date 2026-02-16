"""Create rls_audit_log table and add user account fields

Revision ID: 12_create_rls_audit_log_and_user_fields
Revises: 11_add_api_key_valid_from
Create Date: 2026-02-16

This migration:
1. Creates the rls_audit_log table for RLS bypass audit logging
2. Adds token_version column for session invalidation
3. Adds is_active column for user account management
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "12_create_rls_audit_log_and_user_fields"
down_revision: Union[str, None] = "11_add_api_key_valid_from"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create rls_audit_log table and add user account fields."""
    # ============================================================
    # 1. Create rls_audit_log table
    # ============================================================
    op.create_table(
        "rls_audit_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "uuid",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.String(length=100),
            nullable=True,
            comment="ID of user who performed RLS bypass",
        ),
        sa.Column(
            "reason",
            sa.Text(),
            nullable=False,
            comment="Reason for RLS bypass operation",
        ),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
            comment="When the bypass occurred",
        ),
        sa.Column(
            "ip_address",
            sa.String(length=45),
            nullable=True,
            comment="IP address of the request (if available)",
        ),
        sa.Column(
            "table_name",
            sa.String(length=100),
            nullable=True,
            comment="Table accessed during bypass",
        ),
        sa.Column(
            "operation",
            sa.String(length=50),
            nullable=True,
            comment="Type of operation performed",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("uuid"),
    )

    # Create indexes for rls_audit_log
    op.create_index(op.f("ix_rls_audit_log_id"), "rls_audit_log", ["id"], unique=False)
    op.create_index(
        op.f("ix_rls_audit_log_user_id"),
        "rls_audit_log",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_rls_audit_log_timestamp"),
        "rls_audit_log",
        ["timestamp"],
        unique=False,
    )
    op.create_index(
        "idx_rls_audit_log_recent",
        "rls_audit_log",
        ["timestamp"],
        unique=False,
        postgresql_where=sa.text("timestamp > NOW() - INTERVAL '30 days'"),
    )

    # Add comment to table
    op.execute(
        """
        COMMENT ON TABLE rls_audit_log IS
        'Audit log for Row Level Security bypass operations - for compliance and security investigation'
        """
    )

    # ============================================================
    # 2. Add user account management fields
    # ============================================================
    # Add token_version column for session invalidation
    op.add_column(
        "user",
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
    )

    # Add is_active column for user account management
    op.add_column(
        "user",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
    )

    # Create indexes for user fields
    op.create_index("ix_user_is_active", "user", ["is_active"])
    op.create_index("ix_user_token_version", "user", ["token_version"])


def downgrade() -> None:
    """Drop rls_audit_log table and remove user account fields."""
    # ============================================================
    # 1. Remove user account management fields (reverse order)
    # ============================================================
    op.drop_index("ix_user_token_version", table_name="user")
    op.drop_index("ix_user_is_active", table_name="user")
    op.drop_column("user", "token_version")
    op.drop_column("user", "is_active")

    # ============================================================
    # 2. Drop rls_audit_log table
    # ============================================================
    op.drop_index("idx_rls_audit_log_recent", table_name="rls_audit_log")
    op.drop_index(op.f("ix_rls_audit_log_timestamp"), table_name="rls_audit_log")
    op.drop_index(op.f("ix_rls_audit_log_user_id"), table_name="rls_audit_log")
    op.drop_index(op.f("ix_rls_audit_log_id"), table_name="rls_audit_log")
    op.drop_table("rls_audit_log")
