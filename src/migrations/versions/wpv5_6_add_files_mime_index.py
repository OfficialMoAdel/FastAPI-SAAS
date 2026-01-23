"""Add composite index for files MIME type filtering

Revision ID: wpv5_6_files_mime_index
Revises: wpv5_5_files_composite_index
Create Date: 2026-01-23

Performance Optimization: R2-HYBRID MED-5 - Files MIME Type Index
- Enables O(1) index lookup for MIME type filtering
- Adds composite index on (tenant_id, mime_type)

Query Pattern Optimized:
SELECT * FROM files
WHERE tenant_id = X AND mime_type = 'image/png'

Before: Index scan on tenant_id + filter mime_type (O(N) per tenant)
After: Index scan on (tenant_id, mime_type) - O(log N)

Index Column Order Rationale:
1. tenant_id first - always filtered (multi-tenant isolation)
2. mime_type second - filtered for MIME-based browsing

Use Cases:
- Image gallery: WHERE mime_type LIKE 'image/%'
- Document browser: WHERE mime_type = 'application/pdf'
- Video player: WHERE mime_type LIKE 'video/%'

Migration Strategy:
- Use CREATE INDEX CONCURRENTLY for production (no table locks)
- Index creation is idempotent (IF NOT EXISTS)

Rollback: Drop index via downgrade()
"""

from collections.abc import Sequence

from alembic import op

revision: str = "wpv5_6_files_mime_index"
down_revision: str | None = "wpv5_5_files_composite_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add composite index for files MIME type filtering.

    Uses CONCURRENTLY to avoid table locks in production.
    """
    op.execute(
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_files_tenant_mime
        ON files (tenant_id, mime_type)
        """
    )


def downgrade() -> None:
    """Remove MIME type composite index from files table.

    WARNING: This will revert to slower query execution for MIME filtering.
    """
    op.execute(
        """
        DROP INDEX CONCURRENTLY IF EXISTS ix_files_tenant_mime
        """
    )
