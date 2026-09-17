"""Tier model — canonical home of the tier domain entity (A-M2).

The ``tier`` table historically lived in ``modules/rate_limits/models.py``
because tiers and rate limits co-evolved. That inverted the domain
relationship: rate limits reference tiers (``rate_limit.tier_id`` →
``tier.id``), so the tier entity belongs to the ``tiers`` module.
``rate_limits/models.py`` re-exports ``Tier`` (deprecated, one release)
for import compatibility.
"""

from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.app.core.db.database import Base

if TYPE_CHECKING:
    # A-M2: runtime import would cycle (rate_limits/models re-exports Tier
    # from this module). String reference + TYPE_CHECKING satisfies mypy
    # and SQLAlchemy's lazy mapper resolution.
    from src.app.modules.rate_limits.models import RateLimit


class TierName(str, Enum):
    """Canonical tier identifiers (A5 entitlement seam).

    Values match the ``TIER_RATE_LIMITS`` settings keys; ``FREE`` mirrors
    ``settings.billing.DEFAULT_FREE_TIER_NAME``. ``str`` mixin so members
    compare/serialize as their plain names.
    """

    FREE = "free"
    BASIC = "basic"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class Tier(Base):
    """Tier model defining access tiers and grouping rate limit rules."""

    __tablename__ = "tier"

    id: Mapped[int] = mapped_column("id", autoincrement=True, nullable=False, unique=True, primary_key=True, init=False)
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default_factory=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, onupdate=lambda: datetime.now(UTC)
    )

    rate_limits: Mapped[list["RateLimit"]] = relationship(
        "RateLimit",
        back_populates="tier",
        lazy="noload",  # Don't load by default
        init=False,
    )


# Declarative wiring: RateLimit lives in the rate_limits module and is
# registered by that module's import. The string reference above resolves
# through the shared Base registry once both models are loaded; importing
# this module alone (without rate_limits) yields an unresolved "RateLimit"
# mapper entry, which is fine — it is only configured on access.
