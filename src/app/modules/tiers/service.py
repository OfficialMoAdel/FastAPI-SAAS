"""Tier service layer — composes RateLimitService for tier business logic.

Q-M6: ``TierService`` previously INHERITED from ``RateLimitService`` —
an inverted domain relationship (rate limits are rules attached to tiers,
not the other way around) that forced ``Any``-typed crud reassignment to
keep the two CRUD handles apart. It now composes a ``RateLimitService``
and delegates every tier operation to it, so tier business logic still has
exactly one implementation (in ``rate_limits.service``, which absorbed
the tier methods) while the domain direction is honest: the tiers module
*uses* rate limits, never the reverse.
"""

from typing import Any

from fastcrud import FastCRUD
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.modules.rate_limits.models import RateLimit
from src.app.modules.rate_limits.schemas import RateLimitCreate, RateLimitUpdate, TierCreate, TierUpdate
from src.app.modules.rate_limits.service import RateLimitService
from src.app.modules.tiers.models import Tier


class TierService:
    """Tier domain service delegating tier operations to the composed RateLimitService.

    Attributes:
        crud: Tier-primary CRUD handle (BaseService[Tier]-compatible contract).
        tier_crud: Alias of ``crud`` (P0-1 decoupling regression contract).
        rate_limit_crud: RateLimit CRUD handle for direct rule queries.
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self._rate_limits = RateLimitService(db)
        # BaseService[Tier]-compatible crud handles (P0-1 decoupling):
        # tier operations stay tier-primary even though the business logic
        # is delegated to the composed service.
        self.crud: Any = FastCRUD(Tier)
        self.tier_crud: Any = self.crud
        self.rate_limit_crud: Any = FastCRUD(RateLimit)

    # ------------------------------------------------------------------
    # Tier operations (delegated — single implementation in RateLimitService)
    # ------------------------------------------------------------------

    async def create_tier(self, tier: TierCreate) -> dict[str, Any]:
        """Create a new tier (validation + duplicate guard in the service)."""
        return await self._rate_limits.create_tier(tier)

    async def get_tiers(self, page: int = 1, items_per_page: int = 10) -> dict[str, Any]:
        """Get a paginated list of tiers."""
        return await self._rate_limits.get_tiers(page, items_per_page)

    async def get_tier_by_id(self, tier_id: int) -> dict[str, Any]:
        """Get a tier by ID."""
        return await self._rate_limits.get_tier_by_id(tier_id)

    async def get_tier(self, name: str) -> dict[str, Any]:
        """Get a tier by name."""
        return await self._rate_limits.get_tier(name)

    async def update_tier(self, name: str, values: TierUpdate) -> dict[str, str]:
        """Update a tier (renames rejected — create a new tier instead)."""
        return await self._rate_limits.update_tier(name, values)

    async def delete_tier(self, name: str) -> dict[str, str]:
        """Delete a tier with referential integrity guard."""
        return await self._rate_limits.delete_tier(name)

    # ------------------------------------------------------------------
    # Rate limit access through the tier lens (delegated)
    # ------------------------------------------------------------------

    async def get_rate_limits_by_tier_id(self, tier_id: int) -> dict[str, Any]:
        """Get all rate limits attached to a tier by tier ID."""
        return await self._rate_limits.get_rate_limits_by_tier_id(tier_id)

    async def get_rate_limits(self, tier_name: str, page: int = 1, items_per_page: int = 10) -> dict[str, Any]:
        """Get paginated rate limits for a tier."""
        return await self._rate_limits.get_rate_limits(tier_name, page, items_per_page)

    async def create_rate_limit(self, tier_name: str, rate_limit: RateLimitCreate) -> dict[str, Any]:
        """Create a rate limit attached to a tier."""
        return await self._rate_limits.create_rate_limit(tier_name, rate_limit)

    async def update_rate_limit(self, tier_name: str, rate_limit_id: int, values: RateLimitUpdate) -> dict[str, str]:
        """Update a rate limit attached to a tier."""
        return await self._rate_limits.update_rate_limit(tier_name, rate_limit_id, values)

    async def delete_rate_limit(self, tier_name: str, rate_limit_id: int) -> dict[str, str]:
        """Delete a rate limit attached to a tier."""
        return await self._rate_limits.delete_rate_limit(tier_name, rate_limit_id)


