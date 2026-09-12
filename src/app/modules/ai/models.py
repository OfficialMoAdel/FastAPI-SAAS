"""AI Features database models.

Models for AI chat sessions, messages, and user preferences.
All models follow row-level multi-tenancy via tenant_id foreign key.
"""

import uuid as uuid_pkg
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.app.core.db.database import Base

if TYPE_CHECKING:
    from src.app.modules.tenants.models import Tenant
    from src.app.modules.users.models import User


class AIChatSession(Base):
    """AI conversation container."""

    __tablename__ = "ai_chat_sessions"

    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True, init=False)
    tenant_id: Mapped[uuid_pkg.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), index=True, nullable=False
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)

    title: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    model: Mapped[str] = mapped_column(String(50), nullable=False, default="gemma3")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default_factory=lambda: datetime.now(UTC),
        nullable=False,
        index=True,
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, nullable=True, onupdate=lambda: datetime.now(UTC)
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship(back_populates="ai_chat_sessions", init=False, lazy="noload")
    user: Mapped["User"] = relationship(back_populates="ai_chat_sessions", init=False, lazy="noload")
    ai_token_usage: Mapped[list["AITokenUsage"]] = relationship(back_populates="session", init=False, lazy="noload")
    messages: Mapped[list["AIChatMessage"]] = relationship(
        back_populates="session",
        init=False,
        cascade="all, delete-orphan",
        order_by="AIChatMessage.created_at",
        lazy="noload",
    )


class AIChatMessage(Base):
    """Individual message in an AI chat session."""

    __tablename__ = "ai_chat_messages"

    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True, init=False)
    tenant_id: Mapped[uuid_pkg.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), index=True, nullable=False
    )
    session_id: Mapped[int] = mapped_column(
        ForeignKey("ai_chat_sessions.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    # Required fields (no defaults) must come first
    role: Mapped[str] = mapped_column(String(10), nullable=False)  # user, assistant, system
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Optional fields (with defaults) come after
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default_factory=lambda: datetime.now(UTC),
        nullable=False,
    )

    # Relationships
    session: Mapped["AIChatSession"] = relationship(back_populates="messages", init=False, lazy="noload")


class AISettings(Base):
    """User-level AI preferences.

    One record per user per tenant. Stores preferred model, temperature, max tokens,
    and custom system prompt.
    """

    __tablename__ = "ai_settings"

    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True, init=False)
    tenant_id: Mapped[uuid_pkg.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), index=True, nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, unique=True, nullable=False
    )

    model: Mapped[str] = mapped_column(String(50), nullable=False, default="gemma3")
    temperature: Mapped[float] = mapped_column(Float, nullable=False, default=0.7)
    max_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=2048)
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default_factory=lambda: datetime.now(UTC),
        nullable=False,
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, nullable=True, onupdate=lambda: datetime.now(UTC)
    )

    # Relationships
    user: Mapped["User"] = relationship(back_populates="ai_settings", init=False, lazy="noload")


class AITokenUsage(Base):
    """AI token usage tracking for cost monitoring and analytics.

    Tracks token usage per API call with cost estimation and user/tenant
    attribution for billing and analytics purposes.
    """

    __tablename__ = "ai_token_usage"

    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True, init=False)
    tenant_id: Mapped[uuid_pkg.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id"),
        index=True,
        nullable=True,
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    session_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_chat_sessions.id"),
        index=True,
        nullable=True,
    )

    provider: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(100), nullable=False, index=True)

    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    usage_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default_factory=dict)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default_factory=lambda: datetime.now(UTC),
        nullable=False,
        index=True,
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship(back_populates="ai_token_usage", init=False, lazy="noload")
    user: Mapped["User"] = relationship(back_populates="ai_token_usage", init=False, lazy="noload")
    session: Mapped["AIChatSession"] = relationship(back_populates="ai_token_usage", init=False, lazy="noload")
