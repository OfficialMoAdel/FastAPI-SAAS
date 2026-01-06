"""004_create_sso_settings

Create sso_settings table for host-level OAuth provider configuration.

Note: This is a host-level table without tenant_id (intentional exception
to Constitution VII - SSO settings are shared across all tenants).

Revision ID: 004_sso_settings
Revises: 003_magic_links
Create Date: 2026-01-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "004_sso_settings"
down_revision: str = "003_magic_links"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Create sso_settings table."""
    op.create_table(
        "sso_settings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("client_id", postgresql.BYTEA(), nullable=False),
        sa.Column("client_secret", postgresql.BYTEA(), nullable=False),
        sa.Column("additional_config", postgresql.JSONB(), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default="true"),
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
        sa.PrimaryKeyConstraint("id"),
    )
    # Create unique index on provider
    op.create_index(
        "idx_sso_settings_provider",
        "sso_settings",
        ["provider"],
        unique=True,
    )


def downgrade() -> None:
    """Drop sso_settings table."""
    op.drop_index("idx_sso_settings_provider", table_name="sso_settings")
    op.drop_table("sso_settings")
