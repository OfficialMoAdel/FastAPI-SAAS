from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from src.app.core.db.database import Base


class TokenBlacklist(Base):
    __tablename__ = "token_blacklist"

    id: Mapped[int] = mapped_column("id", autoincrement=True, nullable=False, unique=True, primary_key=True, init=False)
    token: Mapped[str] = mapped_column(String, unique=True, index=True)
    # Task 21: Added index to expires_at to speed up database cleanup sweeps
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    # BUG FIX: tenant_id scopes blacklisted tokens to their originating tenant.
    # Without this, cross-tenant token-reuse was possible if RLS was bypassed.
    # Nullable for backward compatibility with rows created before this migration.
    tenant_id: Mapped[UUID | None] = mapped_column(nullable=True, default=None, index=True)

    __table_args__ = (
        # Speed up tenant-scoped cleanup queries (e.g. token sweep on tenant deletion)
        Index("ix_token_blacklist_tenant_expires", "tenant_id", "expires_at"),
    )

