"""010_add_passkey_credential_id_hash

Add credential_id_hash column for O(log n) passkey lookup.

Security Review: APPROVED (FastAPI_saas-3992)
- SHA256 hash of decrypted credential_id
- credential_id is PUBLIC data (sent in WebAuthn requests)
- No salt needed (high entropy input)

Revision ID: 010_add_passkey_credential_id_hash
Revises: 009_add_ai_tables
Create Date: 2026-01-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "010_add_passkey_credential_id_hash"
down_revision: str = "009_add_ai_tables"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Add credential_id_hash column with index.

    Phase 1: Add nullable column
    Phase 2: Application-level backfill (run separately)
    Phase 3: Index created immediately for new credentials
    Phase 4: After backfill, a separate migration can make column NOT NULL
    """
    # Add nullable hash column (VARCHAR(64) for SHA256 hex digest)
    op.add_column(
        "passkey_credentials",
        sa.Column("credential_id_hash", sa.String(64), nullable=True),
    )

    # Create index for O(log n) lookup
    op.create_index(
        "idx_passkey_credential_id_hash",
        "passkey_credentials",
        ["credential_id_hash"],
    )


def downgrade() -> None:
    """Remove credential_id_hash column and index."""
    op.drop_index("idx_passkey_credential_id_hash", table_name="passkey_credentials")
    op.drop_column("passkey_credentials", "credential_id_hash")
