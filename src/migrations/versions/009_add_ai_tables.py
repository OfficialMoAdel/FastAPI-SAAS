"""Add AI tables for chat sessions, messages, and user settings

Revision ID: 009_add_ai_tables
Revises: 008_add_billing_tables
Create Date: 2025-01-08

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "009_add_ai_tables"
down_revision: str | None = "008_add_billing_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add AI tables: ai_chat_sessions, ai_chat_messages, ai_settings."""

    # 1. Create ai_chat_sessions table
    op.create_table(
        "ai_chat_sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("model", sa.String(length=50), nullable=False, server_default=sa.text("'gemma3'")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_ai_chat_sessions_tenant", "ai_chat_sessions", ["tenant_id"])
    op.create_index("idx_ai_chat_sessions_user", "ai_chat_sessions", ["user_id"])
    op.create_index("idx_ai_chat_sessions_created", "ai_chat_sessions", [sa.text("created_at DESC")])

    # 2. Create ai_chat_messages table
    op.create_table(
        "ai_chat_messages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=10), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.CheckConstraint("role IN ('user', 'assistant', 'system')", name="check_message_role"),
        sa.ForeignKeyConstraint(["session_id"], ["ai_chat_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_ai_chat_messages_session", "ai_chat_messages", ["session_id"])
    op.create_index("idx_ai_chat_messages_created", "ai_chat_messages", ["session_id", sa.text("created_at ASC")])

    # 3. Create ai_settings table (1:1 per user)
    op.create_table(
        "ai_settings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(length=50), nullable=False, server_default=sa.text("'gemma3'")),
        sa.Column("temperature", sa.Float(), nullable=False, server_default=sa.text("0.7")),
        sa.Column("max_tokens", sa.Integer(), nullable=False, server_default=sa.text("2048")),
        sa.Column("system_prompt", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("temperature >= 0.0 AND temperature <= 2.0", name="check_temperature_range"),
        sa.CheckConstraint("max_tokens >= 1 AND max_tokens <= 32000", name="check_max_tokens_range"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_ai_settings_user"),
    )
    op.create_index("idx_ai_settings_user", "ai_settings", ["user_id"], unique=True)


def downgrade() -> None:
    """Remove AI tables."""
    # Drop ai_settings table and indexes
    op.drop_index("idx_ai_settings_user", table_name="ai_settings")
    op.drop_table("ai_settings")

    # Drop ai_chat_messages table and indexes
    op.drop_index("idx_ai_chat_messages_created", table_name="ai_chat_messages")
    op.drop_index("idx_ai_chat_messages_session", table_name="ai_chat_messages")
    op.drop_table("ai_chat_messages")

    # Drop ai_chat_sessions table and indexes
    op.drop_index("idx_ai_chat_sessions_created", table_name="ai_chat_sessions")
    op.drop_index("idx_ai_chat_sessions_user", table_name="ai_chat_sessions")
    op.drop_index("idx_ai_chat_sessions_tenant", table_name="ai_chat_sessions")
    op.drop_table("ai_chat_sessions")
