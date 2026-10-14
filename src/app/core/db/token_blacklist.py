from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.app.core.db.database import Base


class TokenBlacklist(Base):
    __tablename__ = "token_blacklist"

    id: Mapped[int] = mapped_column("id", autoincrement=True, nullable=False, unique=True, primary_key=True, init=False)
    # The raw JWT, not the `jti:` cache member. `jwt_service._check_db_fallback`
    # queries this column directly when a token carries a jti, so it is
    # load-bearing. It must stay unbounded: layer 2 declared VARCHAR(500), but a
    # production-shaped refresh token measures 540-564 characters, which made
    # /api/v1/account/logout fail with a DataError for every user. Migration
    # 09_widen_token_blacklist_token widens the column; declaring it as an
    # unlengthed String here let the two disagree in the first place, so the
    # unbounded type is now explicit on both sides.
    token: Mapped[str] = mapped_column(Text, unique=True, index=True)
    # Task 21: Added index to expires_at to speed up database cleanup sweeps
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    # BUG FIX: tenant_id scopes blacklisted tokens to their originating tenant.
    # Without this, cross-tenant token-reuse was possible if RLS was bypassed.
    # Now non-nullable to ensure all blacklisted tokens are scoped.
    # R2-H7: Added ForeignKey to ensure referential integrity
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)

    __table_args__ = (
        # Speed up tenant-scoped cleanup queries (e.g. token sweep on tenant deletion)
        Index("ix_token_blacklist_tenant_expires", "tenant_id", "expires_at"),
    )
