"""006_create_user_preferences

Create user_preferences table for per-user personalization settings.

Note: Per FR-024, this is a user-scoped table (not tenant-scoped). This is an
intentional exception to Constitution VII - preferences follow the user across
tenants, similar to how sso_settings operate at the host level.

Revision ID: 006_user_preferences
Revises: 005_user_sso_connections
Create Date: 2026-01-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "006_user_preferences"
down_revision: str = "005_user_sso_connections"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


# Default preferences JSON
DEFAULT_PREFERENCES = """{
    "theme": "system",
    "language": "en",
    "email_notifications": true,
    "push_notifications": true,
    "timezone": "UTC",
    "date_format": "YYYY-MM-DD",
    "currency": "USD"
}"""


def upgrade() -> None:
    """Create user_preferences table."""
    op.create_table(
        "user_preferences",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "preferences",
            postgresql.JSONB(),
            nullable=False,
            server_default=DEFAULT_PREFERENCES,
        ),
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
        sa.PrimaryKeyConstraint("id"),
    )
    # Create indexes
    op.create_index(
        "idx_user_preferences_user_id",
        "user_preferences",
        ["user_id"],
        unique=True,
    )
    # GIN index for JSONB queries
    op.create_index(
        "idx_user_preferences_preferences_gin",
        "user_preferences",
        ["preferences"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    """Drop user_preferences table."""
    op.drop_index(
        "idx_user_preferences_preferences_gin",
        table_name="user_preferences",
        postgresql_using="gin",
    )
    op.drop_index("idx_user_preferences_user_id", table_name="user_preferences")
    op.drop_table("user_preferences")
