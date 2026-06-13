"""Layer 5: Content & Storage - Files, user preferences, and feature flags

Revision ID: 05_content_storage
Revises: 04_billing
Create Date: 2026-01-30

Tables: storage_settings, files, user_preferences, feature_flags, feature_flag_overrides
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "05_content_storage"
down_revision: str | None = "04_billing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ===== STORAGE_SETTINGS =====
    op.create_table(
        "storage_settings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("credentials_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("bucket_name", sa.String(length=255), nullable=True),
        sa.Column("region", sa.String(length=50), nullable=True),
        sa.Column("endpoint_url", sa.String(length=500), nullable=True),
        sa.Column("max_file_size_mb", sa.Integer(), nullable=False, server_default=sa.text("50")),
        sa.Column("allowed_file_types", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("storage_quota_bytes", sa.BigInteger(), nullable=False),
        sa.Column("max_file_count", sa.Integer(), nullable=False, server_default=sa.text("10000")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_storage_settings")),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_storage_settings_tenant_id"), ondelete="CASCADE"
        ),
        sa.CheckConstraint("provider IN ('s3', 'azure', 'local')", name=op.f("ck_storage_settings_provider_valid")),
        sa.CheckConstraint("max_file_size_mb > 0", name=op.f("ck_storage_settings_max_file_size_positive")),
    )
    op.create_index("idx_storage_settings_tenant", "storage_settings", ["tenant_id"], unique=True)

    # ===== FILES =====
    op.create_table(
        "files",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("uuid", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("original_name", sa.String(length=255), nullable=False),
        sa.Column("storage_path", sa.String(length=500), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=True),
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_files")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name=op.f("fk_files_tenant_id"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_files_user_id"), ondelete="SET NULL"),
        sa.CheckConstraint("size_bytes >= 0", name=op.f("ck_files_size_non_negative")),
    )
    op.create_index("ix_files_uuid", "files", ["uuid"], unique=True)
    op.create_index("idx_files_tenant", "files", ["tenant_id"])
    op.create_index("idx_files_user", "files", ["user_id"])
    op.create_index("idx_files_tenant_user_created", "files", ["tenant_id", "user_id", "created_at"])
    op.create_index("ix_files_tenant_mime", "files", ["tenant_id", "mime_type"])

    # ===== USER_PREFERENCES =====
    op.create_table(
        "user_preferences",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "preferences", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'")
        ),
        sa.Column("email_notifications_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("push_notifications_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "disabled_notification_types",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column("quiet_hours_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("quiet_hours_start", sa.Time(timezone=True), nullable=True),
        sa.Column("quiet_hours_end", sa.Time(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_preferences")),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_user_preferences_user_id"), ondelete="CASCADE"
        ),
    )
    op.create_index("idx_user_preferences_user_id", "user_preferences", ["user_id"], unique=True)
    op.create_index("idx_user_preferences_quiet_hours", "user_preferences", ["quiet_hours_enabled"])
    op.execute(
        "CREATE INDEX idx_user_preferences_preferences_gin ON user_preferences USING GIN (preferences jsonb_path_ops)"
    )

    # ===== FEATURE_FLAGS =====
    op.create_table(
        "feature_flags",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("uuid", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("default_value", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("value_type", sa.String(length=20), nullable=False, server_default=sa.text("'boolean'")),
        sa.Column("flag_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_feature_flags")),
    )
    op.create_index("idx_feature_flag_key", "feature_flags", ["key"], unique=True)
    op.create_index("idx_feature_flag_uuid", "feature_flags", ["uuid"], unique=True)
    op.create_index("idx_feature_flag_system", "feature_flags", ["is_system"])

    # ===== FEATURE_FLAG_OVERRIDES =====
    op.create_table(
        "feature_flag_overrides",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("uuid", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("feature_flag_id", sa.Integer(), nullable=False),
        sa.Column("custom_value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_feature_flag_overrides")),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_feature_flag_overrides_tenant_id"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["feature_flag_id"],
            ["feature_flags.id"],
            name=op.f("fk_feature_flag_overrides_flag_id"),
            ondelete="CASCADE",
        ),
    )
    op.create_index("idx_feature_flag_override_tenant", "feature_flag_overrides", ["tenant_id"])
    op.create_unique_constraint(
        "uq_tenant_feature_flag_override", "feature_flag_overrides", ["tenant_id", "feature_flag_id"]
    )


def downgrade() -> None:
    op.drop_table("feature_flag_overrides")
    op.drop_table("feature_flags")
    op.drop_table("user_preferences")
    op.drop_table("files")
    op.drop_table("storage_settings")
