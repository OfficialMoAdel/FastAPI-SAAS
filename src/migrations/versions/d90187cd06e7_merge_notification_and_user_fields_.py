"""merge notification and user_fields branches

Revision ID: d90187cd06e7
Revises: 12_create_rls_audit_log_and_user_fields, 2d75ef82a66a
Create Date: 2026-02-21 12:26:41.187001

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd90187cd06e7'
down_revision: Union[str, None] = ('12_create_rls_audit_log_and_user_fields', '2d75ef82a66a')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
