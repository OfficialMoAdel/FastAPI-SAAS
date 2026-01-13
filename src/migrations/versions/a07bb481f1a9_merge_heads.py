"""Merge heads

Revision ID: a07bb481f1a9
Revises: 009_add_ai_tables, c002_perf_idx
Create Date: 2026-01-13 18:50:20.886631

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a07bb481f1a9'
down_revision: Union[str, None] = ('009_add_ai_tables', 'c002_perf_idx')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
