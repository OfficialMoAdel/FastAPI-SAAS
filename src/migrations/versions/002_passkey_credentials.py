"""002_create_passkey_credentials

Create passkey_credentials table for WebAuthn authentication.

Revision ID: 002_passkey_credentials
Revises: 001_auth_user_columns
Create Date: 2026-01-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "002_passkey_credentials"
down_revision: str = "001_auth_user_columns"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Create passkey_credentials table."""
    op.create_table(
        "passkey_credentials",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("credential_id", postgresql.BYTEA(), nullable=False),
        sa.Column("public_key", postgresql.BYTEA(), nullable=False),
        sa.Column("counter", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("device_type", sa.String(100), nullable=False),
        sa.Column("aaguid", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("transports", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("current_timestamp(0)"),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # Create indexes
    op.create_index(
        "idx_passkey_credentials_user_id",
        "passkey_credentials",
        ["user_id"],
    )
    op.create_index(
        "idx_passkey_credentials_credential_id",
        "passkey_credentials",
        ["credential_id"],
        unique=True,
    )


def downgrade() -> None:
    """Drop passkey_credentials table."""
    op.drop_index("idx_passkey_credentials_credential_id", table_name="passkey_credentials")
    op.drop_index("idx_passkey_credentials_user_id", table_name="passkey_credentials")
    op.drop_table("passkey_credentials")
