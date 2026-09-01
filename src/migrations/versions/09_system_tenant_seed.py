"""Layer 9: Seed system tenant for system-level audit logs and RLS bypass

Revision ID: 09_system_tenant_seed
Revises: 08_enable_rls_policies
Create Date: 2026-09-01

This migration seeds a system tenant row (id: 00000000-0000-0000-0000-000000000000)
to ensure foreign key integrity for system-level audit trails and jobs.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "09_system_tenant_seed"
down_revision: str | None = "08_enable_rls_policies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Idempotently insert system tenant
    op.execute(
        """
        INSERT INTO tenants (id, name, slug, is_active, created_at, updated_at)
        VALUES (
            '00000000-0000-0000-0000-000000000000'::UUID,
            'System',
            'system',
            true,
            now(),
            now()
        )
        ON CONFLICT (id) DO NOTHING;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM tenants WHERE id = '00000000-0000-0000-0000-000000000000'::UUID;
        """
    )
