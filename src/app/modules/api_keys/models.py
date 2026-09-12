"""API Key SQLAlchemy model."""

import uuid as uuid_pkg
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.app.core.db.database import Base

if TYPE_CHECKING:
    pass


class APIKey(Base):
    """API Key model for programmatic access to the API.

    Stores hashed API keys with metadata about their usage and permissions.
    The actual key is only shown once at creation time.
    """

    __tablename__ = "api_keys"

    __table_args__ = (
        Index("idx_api_keys_tenant_active", "tenant_id", "is_active"),
        UniqueConstraint("name", "tenant_id", name="uq_api_key_name_tenant"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True, init=False)

    # Key identifiers
    name: Mapped[str] = mapped_column(String(100))
    key_prefix: Mapped[str] = mapped_column(String(8))  # First 8 chars for identification
    hashed_key: Mapped[str] = mapped_column(String(255), unique=True)

    # Ownership
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    tenant_id: Mapped[uuid_pkg.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"))

    # Status and metadata
    is_active: Mapped[bool] = mapped_column(default=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    # Grace period rotation support - key not valid before this timestamp
    valid_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=None,
        doc="Timestamp when key becomes valid (for grace period rotation)",
    )

    # Permissions (optional scopes)
    scopes: Mapped[str | None] = mapped_column(Text, default=None)  # JSON array of scopes

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default_factory=lambda: datetime.now(UTC), init=False
    )

    # Relationships
    user = relationship("User", back_populates="api_keys", lazy="noload")
    tenant = relationship("Tenant", back_populates="api_keys", lazy="noload")

    def __repr__(self) -> str:
        return f"APIKey(id={self.id}, name='{self.name}', prefix='{self.key_prefix}')"
