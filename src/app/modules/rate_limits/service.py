"""Rate limit service layer - encapsulates rate limit business logic (tier operations live in tiers.service)."""

from typing import Any, cast

from fastcrud import compute_offset, paginated_response
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.logger import get_logger
from src.app.core.redis_provider import get_redis
from src.app.core.schemas import MessageResponse
from src.app.modules._base.service import BaseService
from src.app.modules.rate_limits.models import RateLimit
from src.app.modules.rate_limits.schemas import (
    RateLimitCreate,
    RateLimitCreateInternal,
    RateLimitRead,
    RateLimitUpdate,
)
from src.app.modules.tiers.schemas import TierCreate, TierRead, TierUpdate
from src.app.modules.tiers.service import TierService
from src.app.shared.exceptions import DuplicateError, ModelNotFoundError

logger = get_logger(__name__)


class RateLimitService(BaseService[RateLimit]):
    """Service class for rate limits.

    P1-18: tier operations moved to their canonical home
    (``tiers.service.TierService``); this service composes it and keeps
    thin deprecated shims so existing call sites keep working.
    """

    def __init__(self, db: AsyncSession):
        """Initialize RateLimitService with database session.

        Parameters
        ----------
        db : AsyncSession
            Database session
        """
        super().__init__(RateLimit, db, select_schema=RateLimitRead)
        self._tiers = TierService(db)

    # ---------------------------------------------------------------------------
    # Tier Operations (canonical: tiers.service.TierService — deprecated shims)
    # ---------------------------------------------------------------------------

    async def create_tier(self, tier: TierCreate) -> TierRead:
        """Create a new tier (deprecated shim — canonical: TierService.create_tier)."""
        return await self._tiers.create_tier(tier)

    async def get_tiers(self, page: int = 1, items_per_page: int = 10) -> dict[str, Any]:
        """Get paginated list of tiers (deprecated shim — canonical: TierService.get_tiers)."""
        return await self._tiers.get_tiers(page, items_per_page)

    async def get_tier_by_id(self, tier_id: int) -> TierRead:
        """Get a tier by ID (deprecated shim — canonical: TierService.get_tier_by_id)."""
        return await self._tiers.get_tier_by_id(tier_id)

    async def get_tier(self, name: str) -> TierRead:
        """Get a tier by name (deprecated shim — canonical: TierService.get_tier)."""
        return await self._tiers.get_tier(name)

    async def update_tier(self, name: str, values: TierUpdate) -> MessageResponse:
        """Update a tier (deprecated shim — canonical: TierService.update_tier)."""
        return await self._tiers.update_tier(name, values)

    async def delete_tier(self, name: str) -> MessageResponse:
        """Delete a tier (deprecated shim — canonical: TierService.delete_tier)."""
        return await self._tiers.delete_tier(name)

    # ---------------------------------------------------------------------------
    # Rate Limit Operations
    # ---------------------------------------------------------------------------

    async def _get_tier_or_404(self, tier_name: str) -> TierRead:
        """Resolve a tier by name, raising ModelNotFoundError when absent.

        P1-18: routes through the ``get_tier`` shim (canonical
        implementation: TierService) so the tier-resolution seam stays
        patchable at this class — the documented test seam.
        """
        return await self.get_tier(tier_name)

    async def _invalidate_tier_rate_limits_cache(self, tier_id: int) -> None:
        """Invalidate rate limits cache for a tier (B10: retrying, never bare).

        P2-B: also clears the tier-policy cache entry
        (``rate_limit:policy:tier:{id}`` in Redis + the process-local L1 +
        cross-worker publish) via ``invalidate_tier_policy`` — without this
        the policy resolver keeps serving the pre-mutation rules for up to
        the policy TTL after any rate-limit create/update/delete.
        """
        from src.app.core.security.rate_limit_cache import invalidate_tier_policy

        await self._tiers._invalidate_tier_cache([f"rate_limits:tier:{tier_id}"], tier_name=str(tier_id))
        await invalidate_tier_policy(tier_id)

    async def get_rate_limits_by_tier_id(self, tier_id: int) -> dict[str, Any]:
        """Get all rate limits for a tier by tier ID.

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

        # P2-B: lock-based stampede protection. Concurrent cold misses for
        # the same tier used to fan out into N identical DB queries; now a
        # single worker fetches while the rest wait on the lock (the
        # provider client is passed explicitly so the get_redis seam stays
        # the one this service reads).
        from src.app.core.utils.cache import get_with_lock

        async def _fetch() -> dict[str, Any]:
            data = await self.get_page_with_total(tier_id=tier_id)
            return cast(dict[str, Any], data)

        client = get_redis()
        if client is None:
            return await _fetch()
        return cast(dict[str, Any], await get_with_lock(cache_key, _fetch, ttl=3600, client=client))

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
        rate_limit_dict["tier_id"] = db_tier.id

        # Generate unique name if not provided
        if not rate_limit_dict.get("name"):
            clean_path = rate_limit_dict["path"].replace("/", "_").strip("_")
            rate_limit_dict["name"] = f"{tier_name}_{clean_path}_{rate_limit_dict['limit']}_{rate_limit_dict['period']}"

        # Check if rate limit name already exists
        rate_limit_exists = await self.crud.exists(db=self.db, name=rate_limit_dict["name"])
        if rate_limit_exists:
            raise DuplicateError("name", rate_limit_dict["name"])

        # P2-3: Check if rate limit for same path already exists in this tier
        path_exists = await self.crud.exists(db=self.db, tier_id=db_tier.id, path=rate_limit_dict["path"])
        if path_exists:
            raise DuplicateError("path", f"Rate limit for path '{rate_limit_dict['path']}' already exists in this tier")

        rate_limit_internal = RateLimitCreateInternal(**rate_limit_dict)
        created_rate_limit = await self.crud.create(
            db=self.db, object=rate_limit_internal, schema_to_select=RateLimitRead
        )

        if created_rate_limit is None:
            raise ModelNotFoundError("RateLimit", "new")

        # Invalidate cache for this tier
        await self._invalidate_tier_rate_limits_cache(db_tier.id)

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
        """
        db_tier = await self._get_tier_or_404(tier_name)

        offset = compute_offset(page, items_per_page)
        rate_limits_data = await self.get_page_with_total(
            offset=offset,
            limit=items_per_page,
            schema_to_select=RateLimitRead,
            tier_id=db_tier.id,
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
            db=self.db, id=rate_limit_id, tier_id=db_tier.id, schema_to_select=RateLimitRead
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
        DuplicateError
            If new name already exists
        """
        db_tier = await self._get_tier_or_404(tier_name)

        db_rate_limit = await self.crud.get(
            db=self.db, id=rate_limit_id, tier_id=db_tier.id, schema_to_select=RateLimitRead
        )

        if db_rate_limit is None:
            raise ModelNotFoundError("RateLimit", rate_limit_id)

        current_name = (
            db_rate_limit.get("name") if isinstance(db_rate_limit, dict) else getattr(db_rate_limit, "name", None)
        )
        if values.name and values.name != current_name:
            name_exists = await self.crud.exists(db=self.db, name=values.name)
            if name_exists:
                raise DuplicateError("name", values.name)

        await self.crud.update(db=self.db, object=values, id=rate_limit_id, tier_id=db_tier.id)

        # Invalidate cache for this tier
        await self._invalidate_tier_rate_limits_cache(db_tier.id)

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
            db=self.db, id=rate_limit_id, tier_id=db_tier.id, schema_to_select=RateLimitRead
        )

        if db_rate_limit is None:
            raise ModelNotFoundError("RateLimit", rate_limit_id)

        await self.crud.delete(db=self.db, id=rate_limit_id, tier_id=db_tier.id)

        # Invalidate cache for this tier
        await self._invalidate_tier_rate_limits_cache(db_tier.id)

        return {"message": "Rate Limit deleted"}
