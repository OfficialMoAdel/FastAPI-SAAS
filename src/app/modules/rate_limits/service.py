"""Rate limit service layer - encapsulates all rate limit business logic."""

from typing import Any, cast

from fastapi import Depends
from fastcrud import compute_offset, paginated_response
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.db.database import async_get_db
from src.app.core.json_utils import json_dumps, json_loads
from src.app.core.redis_provider import get_redis
from src.app.modules._base.exceptions import DuplicateError, ModelNotFoundError
from src.app.modules._base.service import BaseService
from src.app.modules.rate_limits.models import RateLimit
from src.app.modules.rate_limits.schemas import RateLimitCreate, RateLimitCreateInternal, RateLimitRead, RateLimitUpdate
from src.app.modules.tiers.service import TierService


class RateLimitService(BaseService[RateLimit]):
    """Service class for rate limit-related business logic."""

    def __init__(self, db: AsyncSession):
        """Initialize RateLimitService with database session.

        Parameters
        ----------
        db : AsyncSession
            Database session
        """
        super().__init__(RateLimit, db)

    async def _get_tier_or_404(self, tier_name: str) -> dict[str, Any]:
        """Get tier by name or raise ModelNotFoundError."""
        tier_service = TierService(self.db)
        return cast(dict[str, Any], await tier_service.get_tier(tier_name))

    async def _invalidate_tier_rate_limits_cache(self, tier_id: int) -> None:
        """Invalidate rate limits cache for a tier."""
        client = get_redis()
        if client:
            cache_key = f"rate_limits:tier:{tier_id}"
            await client.delete(cache_key)

    async def get_rate_limits_by_tier_id(self, tier_id: int) -> dict[str, Any]:
        """Get all rate limits for a tier by tier ID.

        This is an interface method for cross-module access.
        Results are cached for 1 hour.

        Parameters
        ----------
        tier_id : int
            Tier ID

        Returns
        -------
        dict[str, Any]
            Rate limits data with 'data' key containing list
        """
        cache_key = f"rate_limits:tier:{tier_id}"

        # Try cache first
        client = get_redis()
        if client:
            cached = await client.get(cache_key)
            if cached:
                return cast(dict[str, Any], json_loads(cached))

        rate_limits_data = await self.crud.get_multi(db=self.db, tier_id=tier_id)

        # Cache result (1 hour TTL)
        if client:
            await client.setex(cache_key, 3600, json_dumps(rate_limits_data, as_str=True))

        return rate_limits_data

    async def create_rate_limit(self, tier_name: str, rate_limit: RateLimitCreate) -> dict[str, Any]:
        """Create a new rate limit for a tier.

        Parameters
        ----------
        tier_name : str
            Name of the tier
        rate_limit : RateLimitCreate
            Rate limit creation data

        Returns
        -------
        dict[str, Any]
            Created rate limit data

        Raises
        ------
        ModelNotFoundError
            If tier not found or creation fails
        DuplicateError
            If rate limit name already exists
        """
        db_tier = await self._get_tier_or_404(tier_name)

        rate_limit_dict = rate_limit.model_dump()
        rate_limit_dict["tier_id"] = db_tier["id"]

        if not rate_limit_dict.get("name"):
            rate_limit_dict["name"] = (
                f"{tier_name}:{rate_limit_dict['path']}:{rate_limit_dict['limit']}:{rate_limit_dict['period']}"
            )

        rate_limit_exists = await self.crud.exists(db=self.db, name=rate_limit_dict["name"])
        if rate_limit_exists:
            raise DuplicateError("name", rate_limit_dict["name"])

        path_exists = await self.crud.exists(
            db=self.db,
            tier_id=db_tier["id"],
            path=rate_limit_dict["path"]
        )
        if path_exists:
            raise DuplicateError("path", f"Rate limit for path '{rate_limit_dict['path']}' already exists on this tier")

        rate_limit_internal = RateLimitCreateInternal(**rate_limit_dict)
        created_rate_limit = await self.crud.create(
            db=self.db, object=rate_limit_internal, schema_to_select=RateLimitRead
        )

        if created_rate_limit is None:
            raise ModelNotFoundError("RateLimit", "new")

        await self._invalidate_tier_rate_limits_cache(db_tier["id"])

        return created_rate_limit

    async def get_rate_limits(self, tier_name: str, page: int = 1, items_per_page: int = 10) -> dict[str, Any]:
        """Get paginated rate limits for a tier.

        Parameters
        ----------
        tier_name : str
            Name of the tier
        page : int
            Page number
        items_per_page : int
            Items per page

        Returns
        -------
        dict[str, Any]
            Paginated rate limits response

        Raises
        ------
        ModelNotFoundError
            If tier not found
        """
        db_tier = await self._get_tier_or_404(tier_name)

        rate_limits_data = await self.crud.get_multi(
            db=self.db,
            offset=compute_offset(page, items_per_page),
            limit=items_per_page,
            tier_id=db_tier["id"],
            schema_to_select=RateLimitRead,
        )

        response: dict[str, Any] = paginated_response(
            crud_data=rate_limits_data, page=page, items_per_page=items_per_page
        )
        return response

    async def get_rate_limit(self, tier_name: str, rate_limit_id: int) -> dict[str, Any]:
        """Get a single rate limit by ID.

        Parameters
        ----------
        tier_name : str
            Name of the tier
        rate_limit_id : int
            Rate limit ID

        Returns
        -------
        dict[str, Any]
            Rate limit data

        Raises
        ------
        ModelNotFoundError
            If tier or rate limit not found
        """
        db_tier = await self._get_tier_or_404(tier_name)

        db_rate_limit = await self.crud.get(
            db=self.db, tier_id=db_tier["id"], id=rate_limit_id, schema_to_select=RateLimitRead
        )
        if db_rate_limit is None:
            raise ModelNotFoundError("RateLimit", rate_limit_id)

        return db_rate_limit

    async def update_rate_limit(self, tier_name: str, rate_limit_id: int, values: RateLimitUpdate) -> dict[str, str]:
        """Update a rate limit.

        Parameters
        ----------
        tier_name : str
            Name of the tier
        rate_limit_id : int
            Rate limit ID
        values : RateLimitUpdate
            Update values

        Returns
        -------
        dict[str, str]
            Success message

        Raises
        ------
        ModelNotFoundError
            If tier or rate limit not found
        """
        db_tier = await self._get_tier_or_404(tier_name)

        db_rate_limit = await self.crud.get(
            db=self.db, tier_id=db_tier["id"], id=rate_limit_id, schema_to_select=RateLimitRead
        )
        if db_rate_limit is None:
            raise ModelNotFoundError("RateLimit", rate_limit_id)

        if values.name and values.name != db_rate_limit.get("name"):
            name_exists = await self.crud.exists(db=self.db, name=values.name)
            if name_exists:
                from src.app.modules._base.exceptions import DuplicateError

                raise DuplicateError("name", values.name)

        await self.crud.update(db=self.db, object=values, id=rate_limit_id)
        await self._invalidate_tier_rate_limits_cache(db_tier["id"])
        return {"message": "Rate Limit updated"}

    async def delete_rate_limit(self, tier_name: str, rate_limit_id: int) -> dict[str, str]:
        """Delete a rate limit.

        Parameters
        ----------
        tier_name : str
            Name of the tier
        rate_limit_id : int
            Rate limit ID

        Returns
        -------
        dict[str, str]
            Success message

        Raises
        ------
        ModelNotFoundError
            If tier or rate limit not found
        """
        db_tier = await self._get_tier_or_404(tier_name)

        db_rate_limit = await self.crud.get(
            db=self.db, tier_id=db_tier["id"], id=rate_limit_id, schema_to_select=RateLimitRead
        )
        if db_rate_limit is None:
            raise ModelNotFoundError("RateLimit", rate_limit_id)

        await self.crud.delete(db=self.db, id=rate_limit_id)
        await self._invalidate_tier_rate_limits_cache(db_tier["id"])
        return {"message": "Rate Limit deleted"}


def get_rate_limit_service(db: AsyncSession = Depends(async_get_db)) -> RateLimitService:
    """Dependency injection factory for RateLimitService.

    Parameters
    ----------
    db : AsyncSession
        Database session from async_get_db

    Returns
    -------
    RateLimitService
        Service instance with database session
    """
    return RateLimitService(db)
