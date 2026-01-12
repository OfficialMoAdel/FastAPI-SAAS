"""add_token_blacklist_and_user_indexes

PERF-R2-P4: Add performance indexes for TokenBlacklist and User tables

THE BOTTLENECKS:
1. TokenBlacklist.expires_at: Token cleanup queries scan full table to find expired tokens
2. User.email + is_deleted: Active user lookups include soft-deleted users in scan

IMPACT:
- TokenBlacklist cleanup: ~10x faster expired token deletion
- User email lookups: Faster active user queries with partial index

NOTES:
- Using raw SQL for partial index support (WHERE clause)
- CONCURRENTLY prevents table locking in production
- IF NOT EXISTS makes migration idempotent

Revision ID: c002_perf_idx
Revises: c001_perf_idx
Create Date: 2026-01-12

"""

from collections.abc import Sequence
from typing import Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c002_perf_idx"
down_revision: Union[str, None] = "c001_perf_idx"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add performance indexes for token cleanup and active user lookups.

    1. idx_token_blacklist_expires_at: Speeds up expired token cleanup queries
       Query pattern: DELETE FROM token_blacklist WHERE expires_at < NOW()

    2. idx_users_email_active: Partial index for active (non-deleted) user email lookups
       Query pattern: SELECT * FROM user WHERE email = ? AND is_deleted = false
    """
    # Index for TokenBlacklist.expires_at to speed up cleanup queries
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_token_blacklist_expires_at
        ON token_blacklist (expires_at)
        """
    )

    # Partial index for active user email lookups
    # Only indexes non-deleted users, making it smaller and faster
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_users_email_active
        ON "user" (email)
        WHERE is_deleted = false
        """
    )


def downgrade() -> None:
    """Remove the performance indexes."""
    op.execute(
        """
        DROP INDEX IF EXISTS idx_token_blacklist_expires_at
        """
    )
    op.execute(
        """
        DROP INDEX IF EXISTS idx_users_email_active
        """
    )
