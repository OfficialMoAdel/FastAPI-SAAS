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
        # FastAPI_saas-auz2: was sa.String(length=500) against the ORM's
        # String(1024). The narrower column refused the width the ORM itself
        # declares, as an untranslated StringDataRightTruncationError, so a key
        # the model accepted became a 500 rather than a validation message.
        # MEASURED on an alembic-built database (perf-results/RESULT-20261018-9cmn/).
        # 1024 is also Azure Blob's maximum blob name, so it is the portable
        # maximum rather than an invented width.
        sa.Column("storage_path", sa.String(length=1024), nullable=False),
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
    # N5: unfiltered file list is WHERE tenant_id = ? ORDER BY created_at DESC;
    # the 3-column idx_files_tenant_user_created cannot serve it (user_id gap).
    op.create_index(
        "idx_files_tenant_created",
        "files",
        ["tenant_id", "created_at"],
        postgresql_ops={"created_at": "DESC"},
    )

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
        # FastAPI_saas-nqk6. These two columns were created as
        # `sa.Time(timezone=True)`, which no part of this application ever used:
        # the ORM maps String(5) (user_preferences/models.py:90,95), crud.py:113
        # writes an "HH:MM" string, notifications/service.py:611-612 parses it
        # with time.fromisoformat, and the schemas type it `str | None` with a
        # `^(0[0-9]|1[0-9]|2[0-3]):[0-5][0-9]$` pattern. MEASURED on the Linux
        # target (perf-local, a scratch database migrated with alembic against a
        # create_all database as control; artefact
        # perf-results/RESULT-20261018-9cmn/, bead FastAPI_saas-9cmn): on a
        # migrated database the INSERT is refused outright with
        # `DatatypeMismatchError: column "quiet_hours_start" is of type time with
        # time zone but expression is of type character varying`, and it is
        # refused with both columns bound to NULL as well, so the failure is the
        # BIND TYPE and no quiet-hours value could ever have been written. A row
        # that does exist reads back as datetime.time, and time.fromisoformat
        # then raises TypeError, which the consumer's `except ValueError` does not
        # catch. The create_all control accepted every write and read, which is
        # why development never sees it.
        #
        # Correcting this declaration is the whole fix HERE, because this project
        # is pre-release: `src/migrations/AGENTS.md` says a schema change should
        # extend the relevant layer's upgrade() rather than stack a new revision,
        # "unless the layer is already applied in production", and it is not.
        # Alembic will not re-run a create_table against an existing database, so
        # a database built before this correction keeps its timetz columns until
        # it is dropped and rebuilt - `DROP SCHEMA public CASCADE` then
        # `alembic upgrade head` is the supported way to get one, and it is what
        # development should do rather than accumulate a repair revision for a
        # schema nobody has shipped. If a layer ever IS applied in production,
        # this becomes the two-part fix: keep this correction AND add a revision
        # that ALTERs the column, because a corrected create_table does not
        # rewrite one that already exists.
        sa.Column("quiet_hours_start", sa.String(length=5), nullable=True),
        sa.Column("quiet_hours_end", sa.String(length=5), nullable=True),
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
