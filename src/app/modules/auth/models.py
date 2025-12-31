"""Password reset token model for secure password recovery."""

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ...core.db.database import Base


def generate_reset_token() -> str:
    """Generate cryptographically secure reset token."""
    return secrets.token_urlsafe(32)


class PasswordResetToken(Base):
    """Token for password reset requests.
    
    Tokens are:
    - Single-use (used flag)
    - Time-limited (expires_at)
    - User-specific (user_id FK)
    - Securely generated (43 chars URL-safe)
    
    Attributes:
        id: Primary key
        user_id: Foreign key to user table
        token: Unique secure token string
        expires_at: Token expiration timestamp (default: 1 hour)
        used: Whether token has been used
        created_at: When token was created
    """
    
    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
        init=False,
    )
    
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    
    token: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
        default_factory=generate_reset_token,
    )
    
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default_factory=lambda: datetime.now(UTC) + timedelta(hours=1),
    )
    
    used: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default_factory=lambda: datetime.now(UTC),
        server_default=text("current_timestamp(0)"),
        init=False,
    )

    # Relationship
    user: Mapped["User"] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "User",
        init=False,
    )

    def __repr__(self) -> str:
        return f"<PasswordResetToken(id={self.id}, user_id={self.user_id}, used={self.used})>"

    @property
    def is_expired(self) -> bool:
        """Check if token has expired."""
        return datetime.now(UTC) > self.expires_at

    @property
    def is_valid(self) -> bool:
        """Check if token is valid (not used and not expired)."""
        return not self.used and not self.is_expired
