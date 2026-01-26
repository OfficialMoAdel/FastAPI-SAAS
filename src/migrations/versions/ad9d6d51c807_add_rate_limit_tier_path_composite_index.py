"""add_rate_limit_tier_path_composite_index

Revision ID: ad9d6d51c807
Revises: d24dd4e73702
Create Date: 2026-01-15 22:37:47.607608

P2-3: Add composite index on rate_limit(tier_id, path) for faster
tier+path lookups during rate limiting checks.
"""

from collections.abc import Sequence
from typing import Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ad9d6d51c807"
down_revision: Union[str, None] = "d24dd4e73702"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # P2-3: Add composite index for tier+path rate limit lookups
    # This index optimizes the common query pattern:
    # SELECT * FROM rate_limit WHERE tier_id = ? AND path = ?
    op.create_index(
        "ix_rate_limit_tier_path",
        "rate_limit",
        ["tier_id", "path"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_rate_limit_tier_path", table_name="rate_limit")
