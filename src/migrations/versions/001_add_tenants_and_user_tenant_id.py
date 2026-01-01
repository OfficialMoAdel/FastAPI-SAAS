"""Add tenants table and user tenant_id column

Revision ID: 001_multitenancy
Revises: None
Create Date: 2025-12-28

Migration Strategy (8 Steps):
1. Create tenants table
2. Insert default "System" tenant with slug "host"
3. Add nullable tenant_id to user table
4. Backfill existing users with default tenant
5. Make tenant_id NOT NULL
6. Add foreign key constraint
7. Drop old email unique constraint
8. Create composite unique index (email, tenant_id)

Rollback: Automatic via transaction on failure. Manual rollback steps in downgrade().
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "001_multitenancy"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Step 1: Create tenants table
    op.create_table(
        "tenants",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("slug", sa.String(length=50), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tenants_slug", "tenants", ["slug"], unique=True)

    # Step 2: Insert default "System" tenant
    op.execute(
        """
        INSERT INTO tenants (name, slug, is_active, created_at)
        VALUES ('System', 'host', TRUE, NOW())
        """
    )

    # Step 3: Add nullable tenant_id column to user table
    op.add_column("user", sa.Column("tenant_id", sa.Integer(), nullable=True))

    # Step 4: Backfill existing users with default tenant
    op.execute(
        """
        UPDATE "user" SET tenant_id = (SELECT id FROM tenants WHERE slug = 'host')
        WHERE tenant_id IS NULL
        """
    )

    # Step 5: Make tenant_id NOT NULL
    op.alter_column("user", "tenant_id", nullable=False)

    # Step 6: Add foreign key constraint
    op.create_foreign_key(
        "fk_user_tenant",
        "user",
        "tenants",
        ["tenant_id"],
        ["id"],
    )

    # Step 7: Drop old email unique constraint
    # First try to drop as index, then as constraint (depends on how it was created)
    op.execute(
        """
        DO $$
        BEGIN
            -- Try to drop as index first
            DROP INDEX IF EXISTS ix_user_email;
            -- Try to drop as constraint
            ALTER TABLE "user" DROP CONSTRAINT IF EXISTS user_email_key;
        EXCEPTION WHEN OTHERS THEN
            -- Ignore errors if neither exists
            NULL;
        END $$;
        """
    )

    # Step 8: Create composite unique constraint and index
    op.create_unique_constraint("uq_user_email_tenant", "user", ["email", "tenant_id"])
    op.create_index("ix_user_tenant_id", "user", ["tenant_id"])

    # Verification query (T028)
    # This will raise an error if any users have NULL tenant_id
    op.execute(
        """
        DO $$
        DECLARE
            null_count INTEGER;
        BEGIN
            SELECT COUNT(*) INTO null_count FROM "user" WHERE tenant_id IS NULL;
            IF null_count > 0 THEN
                RAISE EXCEPTION 'Migration verification failed: % users have NULL tenant_id', null_count;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    """Rollback migration.

    WARNING: Step 3 (recreate global email unique) will FAIL if duplicate emails
    exist across tenants. Manual data cleanup required before rollback in that case.
    """
    # Step 1: Drop composite unique constraint and tenant_id index
    op.drop_index("ix_user_tenant_id", table_name="user")
    op.drop_constraint("uq_user_email_tenant", "user", type_="unique")

    # Step 2: Drop foreign key constraint
    op.drop_constraint("fk_user_tenant", "user", type_="foreignkey")

    # Step 3: Recreate simple unique index on email
    # WARNING: This will fail if same email exists in multiple tenants
    op.create_index("ix_user_email", "user", ["email"], unique=True)

    # Step 4: Drop tenant_id column
    op.drop_column("user", "tenant_id")

    # Step 5: Drop tenants table
    op.drop_index("ix_tenants_slug", table_name="tenants")
    op.drop_table("tenants")
