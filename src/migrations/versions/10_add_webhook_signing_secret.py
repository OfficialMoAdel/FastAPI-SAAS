"""add_webhook_signing_secret

Revision ID: 3c4d5e6f7a8b
Revises: 2bebc73a18eb
Create Date: 2026-02-07 18:00:00.000000

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3c4d5e6f7a8b"
down_revision: Union[str, None] = "2bebc73a18eb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add signing_secret column to webhook_endpoints table."""
    op.add_column(
        "webhook_endpoints",
        sa.Column(
            "signing_secret",
            sa.String(64),
            nullable=True,
            comment="Per-endpoint signing secret for webhook verification (64 chars = 256 bits)",
        ),
    )


def downgrade() -> None:
    """Remove signing_secret column from webhook_endpoints table."""
    op.drop_column("webhook_endpoints", "signing_secret")
