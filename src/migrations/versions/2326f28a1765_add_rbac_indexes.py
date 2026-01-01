"""add_rbac_indexes

Revision ID: 2326f28a1765
Revises: 004_add_tenant_id_to_post
Create Date: 2025-12-30 14:47:46.784005

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '2326f28a1765'
down_revision: str | None = '004_add_tenant_id_to_post'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ### commands manually adjusted ###
    op.create_index(op.f('ix_roles_tenant_id'), 'roles', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_role_permissions_permission_id'), 'role_permissions', ['permission_id'], unique=False)
    op.create_index(op.f('ix_user_roles_tenant_id'), 'user_roles', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_user_roles_role_id'), 'user_roles', ['role_id'], unique=False)
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands manually adjusted ###
    op.drop_index(op.f('ix_user_roles_role_id'), table_name='user_roles')
    op.drop_index(op.f('ix_user_roles_tenant_id'), table_name='user_roles')
    op.drop_index(op.f('ix_role_permissions_permission_id'), table_name='role_permissions')
    op.drop_index(op.f('ix_roles_tenant_id'), table_name='roles')
    # ### end Alembic commands ###
