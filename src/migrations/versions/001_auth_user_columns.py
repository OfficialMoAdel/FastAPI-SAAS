"""001_add_user_columns

Add new columns to users table for authentication enhancements.
- image_url: S3 URL to profile image
- activated_at: Account activation timestamp
- sso_provider_id: Primary SSO provider user was created with

Revision ID: 001_auth_user_columns
Revises: b33b287cc7cb
Create Date: 2026-01-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "001_auth_user_columns"
down_revision: str = "b33b287cc7cb"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Add new columns to users table."""
    # All new columns are nullable (safe for existing data)
    op.add_column(
        "user",
        sa.Column("image_url", sa.String(500), nullable=True),
    )
    op.add_column(
        "user",
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "user",
        sa.Column("sso_provider_id", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    """Remove added columns from users table."""
    op.drop_column("user", "sso_provider_id")
    op.drop_column("user", "activated_at")
    op.drop_column("user", "image_url")
