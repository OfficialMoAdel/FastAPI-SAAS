"""normalize_tenant_slugs

Revision ID: d24dd4e73702
Revises: d5d913ce8ce8
Create Date: 2026-01-14 01:35:44.724808

"""

from collections.abc import Sequence
from typing import Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d24dd4e73702"
down_revision: Union[str, None] = "d5d913ce8ce8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Normalize existing slugs to lowercase and trimmed
    op.execute("UPDATE tenants SET slug = LOWER(TRIM(slug))")

    # Optional: Create index on lower(slug) for performance if not already indexed effectively
    # Since slug is already unique/indexed, this might be redundant unless we want case-insensitive
    # uniqueness explicitly enforced via functional index.
    # For now, just normalizing the data is the primary goal stated in the task.
    # But adding the index is in the task description.
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_tenants_slug_lower ON tenants (LOWER(slug))")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_tenants_slug_lower")
    # Cannot easily revert lowercase normalization without backup
