"""Add valid_from column to api_keys table for grace period rotation.

Revision ID: 11_add_api_key_valid_from
Revises: 10_add_webhook_signing_secret
Create Date: 2026-02-07 18:00:00.000000+00:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "11_add_api_key_valid_from"
down_revision: Union[str, None] = "3c4d5e6f7a8b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add valid_from column to api_keys table.

    This column supports API key rotation with a grace period by allowing
    keys to be created with a future valid_from timestamp, meaning they
    won't be accepted until that time arrives.
    """
    # Add valid_from column to api_keys table
    op.add_column(
        "api_keys",
        sa.Column(
            "valid_from",
            sa.DateTime(timezone=True),
            nullable=True,
            doc="Timestamp when key becomes valid (for grace period rotation). Key is not valid before this time.",
        ),
        schema="public",
    )

    # Create index for efficient queries filtering by valid_from
    op.create_index(
        "idx_api_keys_valid_from",
        "api_keys",
        ["valid_from"],
        schema="public",
    )

    # Also create a composite index for active key lookups with valid_from check
    op.create_index(
        "idx_api_keys_tenant_valid",
        "api_keys",
        ["tenant_id", "is_active", "valid_from"],
        schema="public",
    )


def downgrade() -> None:
    """Remove valid_from column from api_keys table."""
    # Drop indexes first
    op.drop_index("idx_api_keys_tenant_valid", table_name="api_keys", schema="public")
    op.drop_index("idx_api_keys_valid_from", table_name="api_keys", schema="public")

    # Drop column
    op.drop_column("api_keys", "valid_from", schema="public")
