"""UserPreferences SQLAlchemy model for per-user preferences.

Per FR-024 rationale: Preferences follow the user across tenants; this is an
intentional exception to Constitution VII, similar to host-level sso_settings.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.app.core.db.database import Base

if TYPE_CHECKING:
    from src.app.modules.users.models import User


# Default preferences structure
DEFAULT_PREFERENCES = {
    "theme": "system",
    "language": "en",
    "email_notifications": True,
    "push_notifications": True,
    "timezone": "UTC",
    "date_format": "YYYY-MM-DD",
    "currency": "USD",
}

# Default notification preferences
DEFAULT_NOTIFICATION_PREFERENCES: list[str] = []  # No types disabled by default


class UserPreferences(Base):
    """User preferences model for personalization settings.

    Note: Per FR-024, this is a user-scoped table (not tenant-scoped). This is an
    intentional exception to Constitution VII - preferences follow the user across
    tenants, similar to how sso_settings operate at the host level.

    Attributes:
        id: Primary key (auto-increment).
        user_id: Foreign key to user account (unique - one preference record per user).
        preferences: JSONB object containing preference key-value pairs.
        created_at: Preferences creation timestamp.
        updated_at: Last update timestamp.
    """

    __tablename__ = "user_preferences"

    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True, init=False)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    preferences: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default_factory=lambda: DEFAULT_PREFERENCES.copy(),
    )

    # Notification preferences (added per mdl1.2.1)
    email_notifications_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
        init=False,
    )
    push_notifications_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
        init=False,
    )
    disabled_notification_types: Mapped[list[str]] = mapped_column(
        JSONB,
        default_factory=lambda: DEFAULT_NOTIFICATION_PREFERENCES.copy(),
        nullable=False,
        init=False,
    )
    quiet_hours_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        init=False,
    )
    quiet_hours_start: Mapped[str | None] = mapped_column(
        String(5),
        nullable=True,
        init=False,
    )
    quiet_hours_end: Mapped[str | None] = mapped_column(
        String(5),
        nullable=True,
        init=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        init=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=lambda: datetime.now(UTC),
        init=False,
    )

    # Relationships
    user: Mapped["User"] = relationship(init=False, lazy="noload")
