"""MagicLink SQLAlchemy model for magic link tokens.

Stores magic link tokens with expiration tracking for passwordless authentication.
Token validation primarily uses Redis (MagicLinkManager) for performance;
this table provides audit trail and persistence.
"""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.app.core.db.database import Base

if TYPE_CHECKING:
    from src.app.modules.users.models import User


class MagicLink(Base):
    """Magic link token model for passwordless authentication.

    Attributes:
        id: Primary key (auto-increment).
        user_id: Foreign key to user account.
        token: URL-safe base64 token (43 chars from 32 bytes).
        action: Token purpose (login, verify_email, password_reset).
        expires_at: Token expiration timestamp (15 min from creation).
        used_at: Timestamp when token was consumed (null if unused).
        ip_address: Request IP for audit logging.
        user_agent: Request user agent for audit logging.
        created_at: Token creation timestamp.
    """

    __tablename__ = "magic_links"

    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True, init=False)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
    )
    action: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )
    ip_address: Mapped[str | None] = mapped_column(
        INET,
        nullable=True,
        default=None,
    )
    user_agent: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        init=False,
    )

    # Relationships
    user: Mapped["User"] = relationship(init=False, lazy="selectin")
