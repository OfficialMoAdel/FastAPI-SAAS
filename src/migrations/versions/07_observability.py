"""Layer 7: Observability - Audit logs, webhooks, and notifications

Revision ID: 07_observability
Revises: 06_ai_features
Create Date: 2026-01-30

Tables: audit_logs, rls_audit_log, notifications, email_template_overrides, webhook_endpoints, webhook_deliveries
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "07_observability"
down_revision: str | None = "06_ai_features"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ===== AUDIT_LOGS =====
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("entity_type", sa.String(length=100), nullable=True),
        sa.Column("entity_id", sa.String(length=255), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        # FastAPI_saas-auz2: was sa.String(length=500) against the ORM's Text.
        # Text states "no cap" outright, so a capped column contradicts a claim
        # the model made; MEASURED, the column accepted 500 characters and
        # refused 501 (perf-results/RESULT-20261018-9cmn/). A User-Agent over 500
        # characters is ordinary, and the value arrives verbatim from the request
        # header, so nothing upstream of the INSERT can shorten it. Layer 02
        # already declares magic-link user_agent as sa.Text() for the same reason.
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_audit_logs_tenant_id"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_audit_logs_user_id"), ondelete="SET NULL"),
    )
    # B5/P-M2 keyset composite (merged from the former migration 09):
    # covers query_logs ORDER BY created_at DESC, id DESC with the
    # deterministic tie-breaker; replaces the superseded 2-column
    # (tenant_id, created_at) index.
    op.create_index(
        "idx_audit_logs_tenant_created_id",
        "audit_logs",
        ["tenant_id", "created_at", "id"],
        postgresql_ops={"created_at": "DESC", "id": "DESC"},
    )
    op.create_index("idx_audit_tenant_action", "audit_logs", ["tenant_id", "action"])
    op.create_index("idx_audit_user", "audit_logs", ["user_id"])
    op.create_index("idx_audit_action", "audit_logs", ["action"])
    op.create_index("idx_audit_action_created", "audit_logs", ["action", "created_at"])
    op.create_index("idx_audit_entity", "audit_logs", ["entity_type", "entity_id"])
    op.execute("CREATE INDEX idx_audit_details_gin ON audit_logs USING GIN (details)")

    # ===== RLS_AUDIT_LOG =====
    op.create_table(
        "rls_audit_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("uuid", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.String(length=100), nullable=True, comment="ID of user who performed RLS bypass"),
        sa.Column("reason", sa.Text(), nullable=False, comment="Reason for RLS bypass operation"),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="When the bypass occurred",
        ),
        sa.Column("ip_address", sa.String(length=45), nullable=True, comment="IP address of the request"),
        sa.Column("table_name", sa.String(length=100), nullable=True, comment="Table accessed during bypass"),
        sa.Column("operation", sa.String(length=50), nullable=True, comment="Type of operation performed"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rls_audit_log")),
        sa.UniqueConstraint("uuid", name=op.f("uq_rls_audit_log_uuid")),
    )
    op.create_index("ix_rls_audit_log_user_id", "rls_audit_log", ["user_id"])
    op.create_index("ix_rls_audit_log_timestamp", "rls_audit_log", ["timestamp"])
    op.execute(
        """
        COMMENT ON TABLE rls_audit_log IS
        'Audit log for Row Level Security bypass operations - for compliance and security investigation'
        """
    )

    # ===== NOTIFICATIONS =====
    op.create_table(
        "notifications",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("template_name", sa.String(length=100), nullable=False),
        sa.Column("to_email", sa.String(length=255), nullable=False),
        sa.Column("subject", sa.String(length=500), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notifications")),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_notifications_tenant_id"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_notifications_user_id"), ondelete="SET NULL"),
        sa.CheckConstraint("status IN ('pending', 'sent', 'failed')", name=op.f("ck_notifications_status_valid")),
    )
    # B5/P-M2 keyset composite (merged from the former migration 09):
    # serves get_notifications ORDER BY created_at DESC with the id
    # tie-breaker; replaces the superseded 2-column (tenant_id, created_at).
    op.create_index(
        "idx_notifications_tenant_created_id",
        "notifications",
        ["tenant_id", "created_at", "id"],
        postgresql_ops={"created_at": "DESC", "id": "DESC"},
    )
    op.create_index("idx_notification_status", "notifications", ["status"])
    op.create_index("idx_notification_tenant_status", "notifications", ["tenant_id", "status"])
    op.create_index(
        "idx_notification_status_created",
        "notifications",
        ["status", "created_at"],
        postgresql_where=sa.text("status = 'failed'"),
    )

    # ===== EMAIL_TEMPLATE_OVERRIDES =====
    op.create_table(
        "email_template_overrides",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("template_name", sa.String(length=100), nullable=False),
        sa.Column("subject_override", sa.String(length=500), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_email_template_overrides")),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_email_template_overrides_tenant_id"), ondelete="CASCADE"
        ),
    )
    op.create_index("idx_email_template_tenant_name", "email_template_overrides", ["tenant_id", "template_name"])
    op.create_unique_constraint("uq_email_template_tenant", "email_template_overrides", ["tenant_id", "template_name"])

    # ===== WEBHOOK_ENDPOINTS =====
    op.create_table(
        "webhook_endpoints",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        # NOT NULL: the endpoint's HMAC secret is what the receiver verifies
        # the signature with. It was nullable here while the ORM declared it
        # NOT NULL, so the two layers disagreed AND a null secret could be
        # stored — after which every delivery to that endpoint failed at
        # decrypt time, long after the write returned 200. The column moves:
        # the database enforces the invariant the ORM promises.
        sa.Column("secret", sa.String(length=255), nullable=False),
        sa.Column(
            "signing_secret",
            sa.String(255),
            nullable=True,
            comment="Per-endpoint signing secret for webhook verification",
        ),
        sa.Column(
            "old_signing_secret",
            sa.String(255),
            nullable=True,
            comment="Old signing secret for webhook verification",
        ),
        sa.Column("old_secret_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("description", sa.String(length=500), nullable=True),
        # Named `endpoint_metadata`, not `metadata`: SQLAlchemy reserves
        # `metadata` on a declarative base, so a column of that name cannot be
        # mapped by attribute name at all.
        sa.Column("endpoint_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_webhook_endpoints")),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_webhook_endpoints_tenant_id"), ondelete="CASCADE"
        ),
    )
    op.create_index("idx_webhook_endpoint_tenant", "webhook_endpoints", ["tenant_id"])
    op.create_index("idx_webhook_endpoint_active", "webhook_endpoints", ["tenant_id", "is_active"])
    op.create_index(
        "idx_webhook_endpoint_tenant_event",
        "webhook_endpoints",
        ["tenant_id", "event_type", "is_active"],
    )

    # ===== WEBHOOK_DELIVERIES =====
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("webhook_endpoint_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "signature",
            sa.String(length=255),
            nullable=True,
            comment="Deprecated artifact: trigger-time legacy HMAC no longer written; "
            "live signature is computed at send time and never persisted",
        ),
        sa.Column("status", sa.String(length=20), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("response_status", sa.SmallInteger(), nullable=True),
        sa.Column("response_status_code", sa.Integer(), nullable=True),
        sa.Column("response_body", sa.Text(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_webhook_deliveries")),
        sa.ForeignKeyConstraint(
            ["webhook_endpoint_id"],
            ["webhook_endpoints.id"],
            name=op.f("fk_webhook_deliveries_endpoint_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_webhook_deliveries_tenant_id"), ondelete="CASCADE"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'delivered', 'failed')", name=op.f("ck_webhook_deliveries_status_valid")
        ),
    )
    op.create_index("idx_delivery_endpoint", "webhook_deliveries", ["webhook_endpoint_id"])
    op.create_index("idx_delivery_status", "webhook_deliveries", ["status"])
    op.create_index("idx_delivery_created", "webhook_deliveries", ["created_at"])
    op.create_index("idx_delivery_next_retry", "webhook_deliveries", ["next_retry_at"])
    op.create_index(
        "idx_delivery_tenant_status_created",
        "webhook_deliveries",
        ["tenant_id", "status", "created_at"],
        postgresql_ops={"created_at": "DESC"},
    )
    op.create_index(
        "idx_webhook_delivery_retry",
        "webhook_deliveries",
        ["status", "next_retry_at"],
        # The predicate used to add `AND next_retry_at IS NOT NULL`, which
        # excluded exactly the rows the recovery sweep needs: a delivery whose
        # INITIAL enqueue was lost has next_retry_at NULL, and the sweep
        # selects on `coalesce(next_retry_at, created_at)`. The partial index
        # could never serve that population, so the sweep fell back to a
        # sequential scan on a table that grows per webhook. The predicate now
        # covers every PENDING row.
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "idx_webhook_delivery_endpoint_status",
        "webhook_deliveries",
        ["webhook_endpoint_id", "status"],
    )
    op.create_index(
        "idx_webhook_delivery_created",
        "webhook_deliveries",
        ["created_at"],
        postgresql_ops={"created_at": "DESC"},
    )


def downgrade() -> None:
    op.drop_table("webhook_deliveries")
    op.drop_table("webhook_endpoints")
    op.drop_table("email_template_overrides")
    op.drop_table("notifications")
    op.drop_table("rls_audit_log")
    op.drop_table("audit_logs")
