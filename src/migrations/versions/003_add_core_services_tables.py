"""Add core services tables (audit logs, notifications, webhooks, email templates)

Revision ID: 003
Revises: 747ee1b94759
Create Date: 2024-12-29

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "003"
down_revision: str | None = "747ee1b94759"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add core services tables and update tenants table."""
    # 1. Add feature_flags JSONB column to tenants table
    op.add_column(
        "tenants",
        sa.Column(
            "feature_flags",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )

    # 2. Create audit_logs table
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("entity_type", sa.String(length=50), nullable=True),
        sa.Column("entity_id", sa.UUID(), nullable=True),
        sa.Column(
            "details", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )

    # Create indexes for audit_logs
    op.create_index("idx_audit_tenant_created", "audit_logs", ["tenant_id", sa.text("created_at DESC")], unique=False)
    op.create_index("idx_audit_user", "audit_logs", ["user_id"], unique=False)
    op.create_index("idx_audit_action", "audit_logs", ["action"], unique=False)
    op.create_index("idx_audit_entity", "audit_logs", ["entity_type", "entity_id"], unique=False)

    # 3. Create notifications table
    op.create_table(
        "notifications",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("template_name", sa.String(length=100), nullable=False),
        sa.Column("to_email", sa.String(length=255), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.CheckConstraint("status IN ('pending', 'sent', 'failed')", name="check_notification_status"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )

    # Create indexes for notifications
    op.create_index(
        "idx_notification_tenant_created", "notifications", ["tenant_id", sa.text("created_at DESC")], unique=False
    )
    op.create_index("idx_notification_status", "notifications", ["status"], unique=False)
    op.create_index("idx_notification_email", "notifications", ["to_email"], unique=False)

    # 4. Create webhook_endpoints table
    op.create_table(
        "webhook_endpoints",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column("secret", sa.String(length=255), nullable=False),
        sa.Column("event_types", postgresql.ARRAY(sa.String(length=100)), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    # Create indexes for webhook_endpoints
    op.create_index("idx_webhook_endpoint_tenant", "webhook_endpoints", ["tenant_id"], unique=False)
    op.create_index("idx_webhook_endpoint_active", "webhook_endpoints", ["is_active"], unique=False)

    # 5. Create webhook_deliveries table
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("webhook_endpoint_id", sa.UUID(), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("signature", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("response_status_code", sa.Integer(), nullable=True),
        sa.Column("response_body", sa.Text(), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.CheckConstraint("status IN ('pending', 'delivered', 'failed')", name="check_delivery_status"),
        sa.ForeignKeyConstraint(["webhook_endpoint_id"], ["webhook_endpoints.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    # Create indexes for webhook_deliveries
    op.create_index("idx_delivery_endpoint", "webhook_deliveries", ["webhook_endpoint_id"], unique=False)
    op.create_index("idx_delivery_status", "webhook_deliveries", ["status"], unique=False)
    op.create_index("idx_delivery_created", "webhook_deliveries", [sa.text("created_at DESC")], unique=False)
    op.create_index("idx_delivery_next_retry", "webhook_deliveries", ["next_retry_at"], unique=False)

    # 6. Create email_template_overrides table
    op.create_table(
        "email_template_overrides",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("template_name", sa.String(length=100), nullable=False),
        sa.Column("subject_override", sa.String(length=255), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "template_name", name="uq_tenant_template"),
    )

    # Create indexes for email_template_overrides
    op.create_index("idx_template_override_tenant", "email_template_overrides", ["tenant_id"], unique=False)


def downgrade() -> None:
    """Remove core services tables and revert tenants table changes."""
    # Drop tables in reverse order (respecting FK constraints)
    op.drop_index("idx_template_override_tenant", table_name="email_template_overrides")
    op.drop_table("email_template_overrides")

    op.drop_index("idx_delivery_next_retry", table_name="webhook_deliveries")
    op.drop_index("idx_delivery_created", table_name="webhook_deliveries")
    op.drop_index("idx_delivery_status", table_name="webhook_deliveries")
    op.drop_index("idx_delivery_endpoint", table_name="webhook_deliveries")
    op.drop_table("webhook_deliveries")

    op.drop_index("idx_webhook_endpoint_active", table_name="webhook_endpoints")
    op.drop_index("idx_webhook_endpoint_tenant", table_name="webhook_endpoints")
    op.drop_table("webhook_endpoints")

    op.drop_index("idx_notification_email", table_name="notifications")
    op.drop_index("idx_notification_status", table_name="notifications")
    op.drop_index("idx_notification_tenant_created", table_name="notifications")
    op.drop_table("notifications")

    op.drop_index("idx_audit_entity", table_name="audit_logs")
    op.drop_index("idx_audit_action", table_name="audit_logs")
    op.drop_index("idx_audit_user", table_name="audit_logs")
    op.drop_index("idx_audit_tenant_created", table_name="audit_logs")
    op.drop_table("audit_logs")

    # Remove feature_flags column from tenants
    op.drop_column("tenants", "feature_flags")
