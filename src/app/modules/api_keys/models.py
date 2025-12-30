"""API Key SQLAlchemy model."""

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...core.db.database import Base


class APIKey(Base):
    """API Key model for programmatic access to the API.

    Stores hashed API keys with metadata about their usage and permissions.
    The actual key is only shown once at creation time.
    """

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True, init=False)

    # Key identifiers
    name: Mapped[str] = mapped_column(String(100))
    key_prefix: Mapped[str] = mapped_column(String(8))  # First 8 chars for identification
    hashed_key: Mapped[str] = mapped_column(String(255), unique=True)

    # Ownership
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"))
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))

    # Status and metadata
    is_active: Mapped[bool] = mapped_column(default=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    # Permissions (optional scopes)
    scopes: Mapped[str | None] = mapped_column(Text, default=None)  # JSON array of scopes

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default_factory=lambda: datetime.now(UTC), init=False
    )

    def __repr__(self) -> str:
        return f"APIKey(id={self.id}, name='{self.name}', prefix='{self.key_prefix}')"

