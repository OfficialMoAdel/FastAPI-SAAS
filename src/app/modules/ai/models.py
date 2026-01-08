"""AI Features database models.

Models for AI chat sessions, messages, and user preferences.
All models follow row-level multi-tenancy via tenant_id foreign key.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.app.core.db.database import Base

if TYPE_CHECKING:
    from src.app.modules.tenants.models import Tenant
    from src.app.modules.users.models import User


class AIChatSession(Base):
    """AI conversation container."""

    __tablename__ = "ai_chat_sessions"

    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True, init=False)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id"), index=True, nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id"), index=True, nullable=False
    )

    title: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    model: Mapped[str] = mapped_column(String(50), nullable=False, default="gemma3")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default_factory=lambda: datetime.now(UTC),
        nullable=False,
        index=True,
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, nullable=True
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship(back_populates="ai_chat_sessions", init=False)
    user: Mapped["User"] = relationship(back_populates="ai_chat_sessions", init=False)
    messages: Mapped[list["AIChatMessage"]] = relationship(
        back_populates="session",
        init=False,
        cascade="all, delete-orphan",
        order_by="AIChatMessage.created_at",
    )


class AIChatMessage(Base):
    """Individual message in an AI chat session."""

    __tablename__ = "ai_chat_messages"

    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True, init=False)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("ai_chat_sessions.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    # Required fields (no defaults) must come first
    role: Mapped[str] = mapped_column(
        String(10), nullable=False
    )  # user, assistant, system
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Optional fields (with defaults) come after
    token_count: Mapped[int | None] = mapped_column(
        Integer, nullable=True, default=None
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default_factory=lambda: datetime.now(UTC),
        nullable=False,
    )

    # Relationships
    session: Mapped["AIChatSession"] = relationship(back_populates="messages", init=False)


class AISettings(Base):
    """User-level AI preferences.

    One record per user. Stores preferred model, temperature, max tokens,
    and custom system prompt.
    """

    __tablename__ = "ai_settings"

    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True, init=False)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id"), index=True, unique=True, nullable=False
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
        DateTime(timezone=True), default=None, nullable=True
    )

    # Relationships
    user: Mapped["User"] = relationship(back_populates="ai_settings", init=False)
