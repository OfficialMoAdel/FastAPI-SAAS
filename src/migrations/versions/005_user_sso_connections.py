"""005_create_user_sso_connections

Create user_sso_connections table linking users to SSO providers.

Includes tenant_id for row-level isolation per Constitution VII.

Revision ID: 005_user_sso_connections
Revises: 004_sso_settings
Create Date: 2026-01-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "005_user_sso_connections"
down_revision: str = "004_sso_settings"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Create user_sso_connections table."""
    op.create_table(
        "user_sso_connections",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("provider_user_id", sa.String(255), nullable=False),
        sa.Column("provider_email", sa.String(255), nullable=True),
        sa.Column("access_token", postgresql.BYTEA(), nullable=True),
        sa.Column("refresh_token", postgresql.BYTEA(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("current_timestamp(0)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("current_timestamp(0)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # Create indexes
    op.create_index(
        "idx_user_sso_connections_user_id",
        "user_sso_connections",
        ["user_id"],
    )
    op.create_index(
        "idx_user_sso_connections_tenant_id",
        "user_sso_connections",
        ["tenant_id"],
    )
    # Unique constraints
    op.create_unique_constraint(
        "uq_user_sso_user_provider",
        "user_sso_connections",
        ["user_id", "provider"],
    )
    op.create_unique_constraint(
        "uq_user_sso_provider_user",
        "user_sso_connections",
        ["provider", "provider_user_id"],
    )


def downgrade() -> None:
    """Drop user_sso_connections table."""
    op.drop_constraint("uq_user_sso_provider_user", "user_sso_connections", type_="unique")
    op.drop_constraint("uq_user_sso_user_provider", "user_sso_connections", type_="unique")
    op.drop_index("idx_user_sso_connections_tenant_id", table_name="user_sso_connections")
    op.drop_index("idx_user_sso_connections_user_id", table_name="user_sso_connections")
    op.drop_table("user_sso_connections")
