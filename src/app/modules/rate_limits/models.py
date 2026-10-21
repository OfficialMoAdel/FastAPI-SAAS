"""Rate limit models.

A-M2/I7: the ``Tier`` entity lives canonically in
``src/app.modules.tiers.models`` (rate limits reference tiers, not the
reverse). The one-release compatibility re-export of ``Tier`` from this
module is REMOVED — import ``Tier`` from ``modules.tiers.models``.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.app.core.db.database import Base

if TYPE_CHECKING:
    # A-M2/I7: type-only — the runtime re-export was removed to avoid dual
    # mapper registration; the relationship's string "Tier" resolves via
    # the SQLAlchemy mapper registry at runtime.
    from src.app.modules.tiers.models import Tier


class RateLimit(Base, kw_only=True):
    """RateLimit model specifying throughput rules scoped to a Tier."""

    __tablename__ = "rate_limit"
    # P2-3: the unique constraint on (tier_id, path) already builds the
    # index this table is queried by — a separate plain index on the same
    # columns was pure write and storage cost. The creating layer declares
    # the same constraint (03_access_control.py), so both layers agree.
    __table_args__: tuple[Index | UniqueConstraint, ...] = (
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
