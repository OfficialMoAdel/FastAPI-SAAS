"""Tier service layer - delegates to RateLimitService for backward compatibility."""

from typing import Any

from fastapi import Depends
from fastcrud import FastCRUD, paginated_response
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.db.database import async_get_db
from src.app.modules.rate_limits.models import RateLimit, Tier
from src.app.modules.rate_limits.schemas import TierRead
from src.app.modules.rate_limits.service import RateLimitService, get_rate_limit_service


class TierService(RateLimitService):
    """Compatibility service layer delegating tier operations to RateLimitService."""

    def __init__(self, db: AsyncSession):
        super().__init__(db)
        # TierService's primary crud is Tier (preserves BaseService[Tier] contract)
        self.crud: Any = FastCRUD(Tier)
        self.tier_crud: Any = self.crud
        # Rate limits crud stays separate on rate_limit_crud
        self.rate_limit_crud: Any = FastCRUD(RateLimit)

    @property
    def effective_tier_crud(self) -> Any:
        # In TierService, any patch to self.crud is honored for tier operations
        return self.crud

    async def get_tiers(self, page: int = 1, items_per_page: int = 10) -> dict[str, Any]:
        """Get paginated list of tiers using local paginated_response for mock compatibility."""
        crud = self.effective_tier_crud
        tiers_data = await crud.get_multi(
            db=self.db, offset=(page - 1) * items_per_page, limit=items_per_page, schema_to_select=TierRead
        )
        response: dict[str, Any] = paginated_response(crud_data=tiers_data, page=page, items_per_page=items_per_page)
        return response


def get_tier_service(db: AsyncSession = Depends(async_get_db)) -> TierService:
    """Dependency injection factory for TierService."""
    return TierService(db)


__all__ = ["TierService", "get_rate_limit_service", "get_tier_service"]
