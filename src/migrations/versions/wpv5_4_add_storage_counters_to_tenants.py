"""Add storage counters to tenants table for O(1) quota checks

Revision ID: wpv5_4_storage_counters
Revises: d5d913ce8ce8
Create Date: 2025-01-19

Performance Optimization: P0 - File Quota Denormalization
- Eliminates O(N) COUNT + SUM query on every file upload
- Adds denormalized storage_usage_bytes and file_count columns
- Enables O(1) quota checks via simple column reads
- Maintains accuracy via atomic increment/decrement operations

Migration Strategy (4 Steps):
1. Add nullable storage_usage_bytes and file_count columns to tenants
2. Backfill existing tenants with current file statistics
3. Make columns NOT NULL with default 0
4. Add check constraints to prevent negative values

Rollback: Automatic via transaction on failure. Manual rollback in downgrade().
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "wpv5_4_storage_counters"
down_revision: str | None = "d5d913ce8ce8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add storage counter columns to tenants table."""
    # Step 1: Add nullable columns
    op.add_column(
        "tenants",
        sa.Column("storage_usage_bytes", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "tenants",
        sa.Column("file_count", sa.Integer(), nullable=True),
    )

    # Step 2: Backfill existing tenants with current file statistics
    # This calculates the actual values from the files table
    op.execute(
        """
        UPDATE tenants
        SET
            storage_usage_bytes = COALESCE(
                (SELECT SUM(size_bytes) FROM files WHERE files.tenant_id = tenants.id),
                0
            ),
            file_count = COALESCE(
                (SELECT COUNT(*) FROM files WHERE files.tenant_id = tenants.id),
                0
            )
        """
    )

    # Step 3: Make columns NOT NULL with default 0
    op.alter_column("tenants", "storage_usage_bytes", nullable=False, server_default="0")
    op.alter_column("tenants", "file_count", nullable=False, server_default="0")

    # Step 4: Add check constraints to prevent negative values
    op.create_check_constraint(
        "ck_tenants_storage_usage_bytes_non_negative",
        "tenants",
        "storage_usage_bytes >= 0",
    )
    op.create_check_constraint(
        "ck_tenants_file_count_non_negative",
        "tenants",
        "file_count >= 0",
    )

    # Verification query - ensure no NULL values remain
    op.execute(
        """
        DO $$
        DECLARE
            null_storage_count INTEGER;
            null_file_count INTEGER;
        BEGIN
            SELECT COUNT(*) INTO null_storage_count
            FROM tenants
            WHERE storage_usage_bytes IS NULL;

            SELECT COUNT(*) INTO null_file_count
            FROM tenants
            WHERE file_count IS NULL;

            IF null_storage_count > 0 OR null_file_count > 0 THEN
                RAISE EXCEPTION 'Migration verification failed: % tenants have NULL storage_usage_bytes, '
                    '% have NULL file_count',
                    null_storage_count, null_file_count;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    """Remove storage counter columns from tenants table.

    WARNING: This will lose the denormalized counter data.
    The original file statistics can be recalculated from the files table.
    """
    # Step 1: Drop check constraints
    op.drop_constraint("ck_tenants_file_count_non_negative", "tenants", type_="check")
    op.drop_constraint("ck_tenants_storage_usage_bytes_non_negative", "tenants", type_="check")

    # Step 2: Drop columns
    op.drop_column("tenants", "file_count")
    op.drop_column("tenants", "storage_usage_bytes")
