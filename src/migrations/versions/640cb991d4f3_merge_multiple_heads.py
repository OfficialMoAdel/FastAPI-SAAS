"""merge_multiple_heads

Revision ID: 640cb991d4f3
Revises: 010_add_passkey_credential_id_hash, ad9d6d51c807, wpv5_6_files_mime_index
Create Date: 2026-01-25 00:37:32.687948

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '640cb991d4f3'
down_revision: Union[str, None] = ('010_add_passkey_credential_id_hash', 'ad9d6d51c807', 'wpv5_6_files_mime_index')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
