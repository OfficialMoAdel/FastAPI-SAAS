"""Layer 10: Drop superseded overlapping indexes on hot tenant tables.

Revision ID: 10_drop_superseded_indexes
Revises: 09_keyset_composite_indexes
Create Date: 2026-09-09

B5: migration 09 added the (tenant_id, created_at DESC, id DESC) composites
that cover the hot tenant lists' exact ordering. The older 2-column
(tenant_id, created_at*) indexes they supersede were left in place, so every
audit_logs insert maintained three overlapping (tenant_id, created_at*)
indexes. This layer drops the superseded ones. The composites are now also
declared in the models' ``__table_args__`` (audit_logs, notifications) so
the next ``alembic revision --autogenerate`` no longer emits DROP INDEX for
them.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "10_drop_superseded_indexes"
down_revision: str | None = "09_keyset_composite_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Superseded by the 3-column composites created in 09_keyset_composite_indexes.
# Column lists are only used by downgrade() to rebuild the pre-09 shapes.
_SUPERSEDED: list[tuple[str, str, list[str]]] = [
    # Created by 07_observability (plain ASC 2-column).
    ("idx_audit_tenant_created", "audit_logs", ["tenant_id", "created_at"]),
    # Created by 07_observability (plain ASC 2-column).
    ("idx_notification_tenant_created", "notifications", ["tenant_id", "created_at"]),
    # Created by 08_enable_rls_policies (tenant_id, created_at DESC).
    ("idx_audit_logs_tenant_recent", "audit_logs", ["tenant_id", "created_at"]),
]


def upgrade() -> None:
    """Drop the 2-column indexes superseded by the 09 composites."""
    for index_name, _table, _columns in _SUPERSEDED:
        op.execute(f'DROP INDEX IF EXISTS "{index_name}"')


def downgrade() -> None:
    """Recreate the superseded 2-column indexes in their original shapes."""
    op.execute(
        'CREATE INDEX IF NOT EXISTS "idx_audit_tenant_created" '
        'ON "audit_logs" ("tenant_id", "created_at")'
    )
    op.execute(
        'CREATE INDEX IF NOT EXISTS "idx_notification_tenant_created" '
        'ON "notifications" ("tenant_id", "created_at")'
    )
    op.execute(
        'CREATE INDEX IF NOT EXISTS "idx_audit_logs_tenant_recent" '
        'ON "audit_logs" ("tenant_id", "created_at" DESC)'
    )
