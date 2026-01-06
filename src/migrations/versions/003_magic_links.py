"""003_create_magic_links

Create magic_links table for passwordless authentication.

Revision ID: 003_magic_links
Revises: 002_passkey_credentials
Create Date: 2026-01-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "003_magic_links"
down_revision: str = "002_passkey_credentials"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Create magic_links table."""
    op.create_table(
        "magic_links",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token", sa.String(64), nullable=False),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
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
        "idx_magic_links_token",
        "magic_links",
        ["token"],
        unique=True,
    )
    op.create_index(
        "idx_magic_links_user_id",
        "magic_links",
        ["user_id"],
    )
    op.create_index(
        "idx_magic_links_expires_at",
        "magic_links",
        ["expires_at"],
    )


def downgrade() -> None:
    """Drop magic_links table."""
    op.drop_index("idx_magic_links_expires_at", table_name="magic_links")
    op.drop_index("idx_magic_links_user_id", table_name="magic_links")
    op.drop_index("idx_magic_links_token", table_name="magic_links")
    op.drop_table("magic_links")
