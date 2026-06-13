"""Layer 6: AI Features - AI chat and settings

Revision ID: 06_ai_features
Revises: 05_content_storage
Create Date: 2026-01-30

Tables: ai_chat_sessions, ai_chat_messages, ai_settings
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "06_ai_features"
down_revision: str | None = "05_content_storage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ===== AI_CHAT_SESSIONS =====
    op.create_table(
        "ai_chat_sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("uuid", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_chat_sessions")),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_ai_chat_sessions_tenant_id"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_ai_chat_sessions_user_id"), ondelete="CASCADE"
        ),
    )
    op.create_index("ix_ai_chat_sessions_uuid", "ai_chat_sessions", ["uuid"], unique=True)
    op.create_index("idx_ai_chat_sessions_tenant", "ai_chat_sessions", ["tenant_id"])
    op.create_index("idx_ai_chat_sessions_user", "ai_chat_sessions", ["user_id"])
    op.create_index("idx_ai_chat_sessions_created", "ai_chat_sessions", ["created_at"])

    # ===== AI_CHAT_MESSAGES =====
    op.create_table(
        "ai_chat_messages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("uuid", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_chat_messages")),
        sa.ForeignKeyConstraint(
            ["session_id"], ["ai_chat_sessions.id"], name=op.f("fk_ai_chat_messages_session_id"), ondelete="CASCADE"
        ),
        sa.CheckConstraint("role IN ('user', 'assistant', 'system')", name=op.f("ck_ai_chat_messages_role_valid")),
    )
    op.create_index("ix_ai_chat_messages_uuid", "ai_chat_messages", ["uuid"], unique=True)
    op.create_index("idx_ai_chat_messages_session", "ai_chat_messages", ["session_id"])
    op.create_index("idx_ai_chat_messages_session_created", "ai_chat_messages", ["session_id", "created_at"])

    # ===== AI_SETTINGS =====
    op.create_table(
        "ai_settings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False, server_default=sa.text("'gpt-4'")),
        sa.Column("temperature", sa.Float(), nullable=False, server_default=sa.text("0.7")),
        sa.Column("max_tokens", sa.Integer(), nullable=False, server_default=sa.text("4096")),
        sa.Column("system_prompt", sa.Text(), nullable=True),
        sa.Column("preferences", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_settings")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_ai_settings_user_id"), ondelete="CASCADE"),
        sa.CheckConstraint("temperature >= 0.0 AND temperature <= 2.0", name=op.f("ck_ai_settings_temperature_valid")),
        sa.CheckConstraint("max_tokens >= 1 AND max_tokens <= 32000", name=op.f("ck_ai_settings_max_tokens_valid")),
    )
    # ===== AI_TOKEN_USAGE =====
    op.create_table(
        "ai_token_usage",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("session_id", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default=sa.text("0.0")),
        sa.Column(
            "usage_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_token_usage")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name=op.f("fk_ai_token_usage_tenant_id")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_ai_token_usage_user_id")),
        sa.ForeignKeyConstraint(["session_id"], ["ai_chat_sessions.id"], name=op.f("fk_ai_token_usage_session_id")),
    )
    op.create_index("idx_ai_token_usage_tenant", "ai_token_usage", ["tenant_id"])
    op.create_index("idx_ai_token_usage_user", "ai_token_usage", ["user_id"])
    op.create_index("idx_ai_token_usage_session", "ai_token_usage", ["session_id"])
    op.create_index("idx_ai_token_usage_provider", "ai_token_usage", ["provider"])
    op.create_index("idx_ai_token_usage_model", "ai_token_usage", ["model"])
    op.create_index("idx_ai_token_usage_created", "ai_token_usage", ["created_at"])


def downgrade() -> None:
    op.drop_table("ai_token_usage")
    op.drop_table("ai_settings")
    op.drop_table("ai_chat_messages")
    op.drop_table("ai_chat_sessions")
