"""Rate limit models.

A-M2: the ``Tier`` entity now lives canonically in
``src/app/modules/tiers/models.py`` (rate limits reference tiers, not the
reverse). The import below re-exports it from here for one release so
existing ``from src.app.modules.rate_limits.models import Tier`` sites keep
working; new code must import from ``modules.tiers.models``.
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.app.core.db.database import Base
from src.app.modules.tiers.models import Tier  # noqa: F401  (deprecated re-export, A-M2)


class RateLimit(Base, kw_only=True):
    """RateLimit model specifying throughput rules scoped to a Tier."""

    __tablename__ = "rate_limit"
    # P2-3: Composite index for tier+path lookups (common query pattern)
    # Unique constraint to prevent duplicate rate limits for same path in tier
    __table_args__: tuple[Index | UniqueConstraint, ...] = (
        Index("ix_rate_limit_tier_path", "tier_id", "path"),
        UniqueConstraint("tier_id", "path", name="uq_rate_limit_tier_path"),
    )

    id: Mapped[int] = mapped_column("id", autoincrement=True, nullable=False, unique=True, primary_key=True, init=False)
    tier_id: Mapped[int] = mapped_column(ForeignKey("tier.id"), index=True)
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    path: Mapped[str] = mapped_column(String, nullable=False)
    limit: Mapped[int] = mapped_column(Integer, nullable=False)
    period: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default_factory=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, onupdate=lambda: datetime.now(UTC)
    )

    tier: Mapped["Tier"] = relationship("Tier", back_populates="rate_limits", init=False, lazy="noload")
