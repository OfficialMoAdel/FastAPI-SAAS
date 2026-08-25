"""P6: functional indexes for case-insensitive user lookups.

`users/crud.py` authenticates via ``func.lower(col) = <lowered param>``.
Plain b-tree indexes on (username)/(email) cannot serve that predicate —
Postgres would seq-scan. These expression indexes match the query shape
exactly, keeping login lookups O(log n) per tenant.

Revision ID: 13_user_lower_indexes
Revises: 12_pricing_rls_stripe_secret
"""

from collections.abc import Sequence

from alembic import op

revision: str = "13_user_lower_indexes"
down_revision: str = "12_pricing_rls_stripe_secret"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Tenant-scoped lower(email): matches the auth lookup path
    # (tenant filter + lower(email) = param).
    op.create_index(
        "idx_users_tenant_lower_email",
        "users",
        ["tenant_id", op.text("lower(email)")],
        unique=False,
    )
    # Global lower(username): username lookups are tenant-optional.
    op.create_index(
        "idx_users_lower_username",
        "users",
        [op.text("lower(username)")],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_users_lower_username", table_name="users")
    op.drop_index("idx_users_tenant_lower_email", table_name="users")
