import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.app.core.db.database import Base

if TYPE_CHECKING:
    from src.app.modules.users.models import User


class MFAMethod(Base):
    """MFA Method model.

    Stores Multi-Factor Authentication methods for users (e.g., TOTP).
    """

    __tablename__ = "mfa_methods"

    # Primary key with default (init=False)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, init=False)

    # Required fields
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=False, index=True)
    secret: Mapped[str] = mapped_column(String, nullable=False)

    # Optional fields with defaults (init=False)
    name: Mapped[str | None] = mapped_column(String(50), nullable=True, init=False)
    type: Mapped[str] = mapped_column(String(20), default="totp", nullable=False, init=False)
    backup_codes: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True, default=list, init=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, init=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, init=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), init=False)

    # Relationships (init=False)
    user: Mapped["User"] = relationship(back_populates="mfa_methods", init=False)
