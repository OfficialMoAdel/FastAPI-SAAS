"""Add tenant_id to post table

Revision ID: 004_add_tenant_id_to_post
Revises: 003
Create Date: 2025-12-30

"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '004_add_tenant_id_to_post'
down_revision: Union[str, None] = '003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add tenant_id column to post table with foreign key to tenants.

    For existing posts, we need to populate tenant_id based on the
    created_by_user_id's tenant. This migration:
    1. Adds tenant_id as nullable first
    2. Populates it from the user's tenant
    3. Makes it non-nullable
    4. Adds index
    """
    # Step 1: Add nullable tenant_id column
    op.add_column('post', sa.Column('tenant_id', sa.Integer(), nullable=True))

    # Step 2: Create foreign key constraint
    op.create_foreign_key(
        'fk_post_tenant_id_tenants',
        'post', 'tenants',
        ['tenant_id'], ['id']
    )

    # Step 3: Populate tenant_id from user's tenant for existing posts
    # This SQL updates all posts to have the tenant_id of their creator
    op.execute("""
        UPDATE post
        SET tenant_id = (
            SELECT "user".tenant_id
            FROM "user"
            WHERE "user".id = post.created_by_user_id
        )
        WHERE tenant_id IS NULL
    """)

    # Step 4: Make tenant_id non-nullable after population
    op.alter_column('post', 'tenant_id', nullable=False)

    # Step 5: Add index for efficient tenant queries
    op.create_index('ix_post_tenant_id', 'post', ['tenant_id'])


def downgrade() -> None:
    """Remove tenant_id from post table."""
    op.drop_index('ix_post_tenant_id', table_name='post')
    op.drop_constraint('fk_post_tenant_id_tenants', 'post', type_='foreignkey')
    op.drop_column('post', 'tenant_id')
