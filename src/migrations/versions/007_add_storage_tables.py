"""Add storage_settings and files tables for cloud storage management

Revision ID: 007_storage_tables
Revises: 006_user_preferences
Create Date: 2025-01-07

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "007_storage_tables"
down_revision: str | None = "006_user_preferences"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add storage_settings and files tables."""
    # 1. Create storage_settings table
    op.create_table(
        "storage_settings",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("credentials_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("bucket_name", sa.String(length=255), nullable=True),
        sa.Column("region", sa.String(length=50), nullable=True),
        sa.Column("max_file_size_mb", sa.Integer(), nullable=False, server_default=sa.text("100")),
        sa.Column(
            "allowed_file_types",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text('\'["image/jpeg", "image/png", "image/gif", "application/pdf"]\'::jsonb'),
        ),
        sa.Column("max_file_count", sa.Integer(), nullable=False, server_default=sa.text("10000")),
        sa.Column("storage_quota_bytes", sa.BigInteger(), nullable=False, server_default=sa.text("10737418240")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.CheckConstraint("provider IN ('s3', 'azure', 'local')", name="check_storage_provider"),
        sa.CheckConstraint("max_file_size_mb > 0 AND max_file_size_mb <= 5120", name="check_max_file_size"),
        sa.CheckConstraint("max_file_count > 0", name="check_max_file_count"),
        sa.CheckConstraint("storage_quota_bytes > 0", name="check_storage_quota"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", name="uq_storage_settings_tenant"),
    )

    # Create index for storage_settings
    op.create_index("idx_storage_settings_tenant", "storage_settings", ["tenant_id"], unique=True)

    # 2. Create files table
    op.create_table(
        "files",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("original_name", sa.String(length=255), nullable=False),
        sa.Column("storage_path", sa.String(length=1024), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.CheckConstraint("size_bytes >= 0", name="check_file_size"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_path", name="uq_file_storage_path"),
    )

    # Create indexes for files
    op.create_index("idx_files_tenant", "files", ["tenant_id"], unique=False)
    op.create_index("idx_files_user", "files", ["user_id"], unique=False)
    op.create_index("idx_files_mime_type", "files", ["mime_type"], unique=False)
    op.create_index("idx_files_tenant_public", "files", ["tenant_id", "is_public"], unique=False)
    op.create_index("idx_files_created", "files", [sa.text("created_at DESC")], unique=False)


def downgrade() -> None:
    """Remove storage_settings and files tables."""
    # Drop files table and indexes
    op.drop_index("idx_files_created", table_name="files")
    op.drop_index("idx_files_tenant_public", table_name="files")
    op.drop_index("idx_files_mime_type", table_name="files")
    op.drop_index("idx_files_user", table_name="files")
    op.drop_index("idx_files_tenant", table_name="files")
    op.drop_table("files")

    # Drop storage_settings table and indexes
    op.drop_index("idx_storage_settings_tenant", table_name="storage_settings")
    op.drop_table("storage_settings")
