"""Rate limit service layer - encapsulates all rate limit business logic."""

from typing import Any

from fastcrud import compute_offset, paginated_response
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.exceptions.http_exceptions import DuplicateValueException, NotFoundException
from .crud import crud_rate_limits
from .schemas import RateLimitCreate, RateLimitCreateInternal, RateLimitRead, RateLimitUpdate


class RateLimitService:
    """Service class for rate limit-related business logic."""

    async def _get_tier_or_404(self, tier_name: str, db: AsyncSession) -> dict[str, Any]:
        """Get tier by name or raise NotFoundException."""
        # Use tier_service for cross-module access
        from ...modules.tiers.service import tier_service

        return await tier_service.get_tier(tier_name, db)

    async def get_rate_limits_by_tier_id(self, tier_id: int, db: AsyncSession) -> dict[str, Any]:
        """Get all rate limits for a tier by tier ID.

        This is an interface method for cross-module access.

        Parameters
        ----------
        tier_id : int
            Tier ID
        db : AsyncSession
            Database session

        Returns
        -------
        dict[str, Any]
            Rate limits data with 'data' key containing list
        """
        rate_limits_data = await crud_rate_limits.get_multi(db=db, tier_id=tier_id)
        return rate_limits_data

    async def create_rate_limit(self, tier_name: str, rate_limit: RateLimitCreate, db: AsyncSession) -> dict[str, Any]:
        """Create a new rate limit for a tier.

        Parameters
        ----------
        tier_name : str
            Name of the tier
        rate_limit : RateLimitCreate
            Rate limit creation data
        db : AsyncSession
            Database session

        Returns
        -------
        dict[str, Any]
            Created rate limit data

        Raises
        ------
        NotFoundException
            If tier not found or creation fails
        DuplicateValueException
            If rate limit name already exists
        """
        db_tier = await self._get_tier_or_404(tier_name, db)

        rate_limit_dict = rate_limit.model_dump()
        rate_limit_dict["tier_id"] = db_tier["id"]

        rate_limit_exists = await crud_rate_limits.exists(db=db, name=rate_limit_dict["name"])
        if rate_limit_exists:
            raise DuplicateValueException("Rate Limit Name not available")

        rate_limit_internal = RateLimitCreateInternal(**rate_limit_dict)
        created_rate_limit = await crud_rate_limits.create(
            db=db, object=rate_limit_internal, schema_to_select=RateLimitRead
        )

        if created_rate_limit is None:
            raise NotFoundException("Failed to create rate limit")

        return created_rate_limit

    async def get_rate_limits(
        self, tier_name: str, db: AsyncSession, page: int = 1, items_per_page: int = 10
    ) -> dict[str, Any]:
        """Get paginated rate limits for a tier.

        Parameters
        ----------
        tier_name : str
            Name of the tier
        db : AsyncSession
            Database session
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
        NotFoundException
            If tier not found
        """
        db_tier = await self._get_tier_or_404(tier_name, db)

        rate_limits_data = await crud_rate_limits.get_multi(
            db=db,
            offset=compute_offset(page, items_per_page),
            limit=items_per_page,
            tier_id=db_tier["id"],
        )

        response: dict[str, Any] = paginated_response(
            crud_data=rate_limits_data, page=page, items_per_page=items_per_page
        )
        return response

    async def get_rate_limit(self, tier_name: str, rate_limit_id: int, db: AsyncSession) -> dict[str, Any]:
        """Get a single rate limit by ID.

        Parameters
        ----------
        tier_name : str
            Name of the tier
        rate_limit_id : int
            Rate limit ID
        db : AsyncSession
            Database session

        Returns
        -------
        dict[str, Any]
            Rate limit data

        Raises
        ------
        NotFoundException
            If tier or rate limit not found
        """
        db_tier = await self._get_tier_or_404(tier_name, db)

        db_rate_limit = await crud_rate_limits.get(
            db=db, tier_id=db_tier["id"], id=rate_limit_id, schema_to_select=RateLimitRead
        )
        if db_rate_limit is None:
            raise NotFoundException("Rate Limit not found")

        return db_rate_limit

    async def update_rate_limit(
        self, tier_name: str, rate_limit_id: int, values: RateLimitUpdate, db: AsyncSession
    ) -> dict[str, str]:
        """Update a rate limit.

        Parameters
        ----------
        tier_name : str
            Name of the tier
        rate_limit_id : int
            Rate limit ID
        values : RateLimitUpdate
            Update values
        db : AsyncSession
            Database session

        Returns
        -------
        dict[str, str]
            Success message

        Raises
        ------
        NotFoundException
            If tier or rate limit not found
        """
        db_tier = await self._get_tier_or_404(tier_name, db)

        db_rate_limit = await crud_rate_limits.get(
            db=db, tier_id=db_tier["id"], id=rate_limit_id, schema_to_select=RateLimitRead
        )
        if db_rate_limit is None:
            raise NotFoundException("Rate Limit not found")

        await crud_rate_limits.update(db=db, object=values, id=rate_limit_id)
        return {"message": "Rate Limit updated"}

    async def delete_rate_limit(self, tier_name: str, rate_limit_id: int, db: AsyncSession) -> dict[str, str]:
        """Delete a rate limit.

        Parameters
        ----------
        tier_name : str
            Name of the tier
        rate_limit_id : int
            Rate limit ID
        db : AsyncSession
            Database session

        Returns
        -------
        dict[str, str]
            Success message

        Raises
        ------
        NotFoundException
            If tier or rate limit not found
        """
        db_tier = await self._get_tier_or_404(tier_name, db)

        db_rate_limit = await crud_rate_limits.get(
            db=db, tier_id=db_tier["id"], id=rate_limit_id, schema_to_select=RateLimitRead
        )
        if db_rate_limit is None:
            raise NotFoundException("Rate Limit not found")

        await crud_rate_limits.delete(db=db, id=rate_limit_id)
        return {"message": "Rate Limit deleted"}


# Singleton instance for import convenience
rate_limit_service = RateLimitService()
