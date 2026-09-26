"""Rate limit service layer - encapsulates rate limit business logic (tier operations live in tiers.service)."""

from typing import Any, cast

from fastcrud import compute_offset, paginated_response
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.cache.client import cache_client
from src.app.core.cache.invalidation_exhaustion import (
    ExhaustionMarker,
    InvalidationPolicy,
    invalidate_with_exhaustion,
)
from src.app.core.logger import get_logger
from src.app.core.redis_provider import get_redis
from src.app.modules._base.service import BaseService
from src.app.modules.rate_limits.models import RateLimit
from src.app.modules.rate_limits.schemas import (
    RateLimitCreate,
    RateLimitCreateInternal,
    RateLimitRead,
    RateLimitUpdate,
)
from src.app.modules.tiers.schemas import TierRead
from src.app.modules.tiers.service import TierService
from src.app.shared.exceptions import DuplicateError, ModelNotFoundError

logger = get_logger(__name__)

#: The rate-limit key family's DECLARED exhaustion policy. Same stale-window
#: concern as tiers, same log-only choice: the error record is the signal and
#: no poison-key marker is written for this family.
_RATE_LIMIT_INVALIDATION_POLICY = InvalidationPolicy(
    family="Rate limit",
    on_exhaustion=ExhaustionMarker.LOG_ONLY,
    exhausted_message="Rate limit cache invalidation failed after retry — stale rules may persist until TTL",
)


class RateLimitService(BaseService[RateLimit]):
    """Service class for rate limits.

    P1-18: tier operations live in their canonical home
    (``tiers.service.TierService``), composed here as ``self._tiers``.
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
    # Rate Limit Operations
    # ---------------------------------------------------------------------------

    async def _get_tier_or_404(self, tier_name: str) -> TierRead:
        """Resolve a tier by name, raising ModelNotFoundError when absent.

        Canonical implementation lives in the composed TierService; the
        seam stays patchable at ``self._tiers.get_tier``.
        """
        return await self._tiers.get_tier(tier_name)

    async def _invalidate_tier_rate_limits_cache(self, tier_id: int) -> None:
        """Invalidate every cache keyed on this tier's rate-limit rules.

        I6: owns BOTH families locally instead of reaching into TierService's
        private ``_invalidate_tier_cache``:
        - ``rate_limits:tier:{id}`` — the rule-list read-through cache;
        - ``rate_limit:policy:tier:{id}`` + L1 — the compiled zero-DB policy
          cache (P2-B: without this the resolver serves pre-mutation rules
          until TTL after any create/update/delete).

        B10 posture preserved: retry once, then error-log so the stale window
        is operator-visible — never fail-open on a swallowed DELETE. The retry
        / visibility policy lives in ``core.cache.invalidation_exhaustion``;
        this family DECLARES log-only exhaustion (no poison-key marker) on
        the policy row above.
        """
        keys = [f"rate_limits:tier:{tier_id}"]

        async def _invalidate_redis() -> None:
            await cache_client.delete(*keys)

        await invalidate_with_exhaustion(
            _invalidate_redis,
            policy=_RATE_LIMIT_INVALIDATION_POLICY,
            log_context={"tier_id": tier_id, "keys": keys},
        )

        from src.app.core.security.rate_limit_cache import invalidate_tier_policy

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
