"""Rate limit service layer - encapsulates all rate limit and tier business logic."""

from typing import Any, cast

from fastcrud import FastCRUD, compute_offset, paginated_response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.json_utils import json_dumps, json_loads
from src.app.core.logger import get_logger
from src.app.core.redis_provider import get_redis
from src.app.modules._base.exceptions import DuplicateError, ModelNotFoundError
from src.app.modules._base.service import BaseService
from src.app.modules.rate_limits.models import RateLimit, Tier
from src.app.modules.rate_limits.schemas import (
    RateLimitCreate,
    RateLimitCreateInternal,
    RateLimitRead,
    RateLimitUpdate,
    TierCreate,
    TierCreateInternal,
    TierRead,
    TierUpdate,
)

logger = get_logger(__name__)


class RateLimitService(BaseService[RateLimit]):
    """Deep service class for rate limits and tier management."""

    def __init__(self, db: AsyncSession):
        """Initialize RateLimitService with database session.

        Parameters
        ----------
        db : AsyncSession
            Database session
        """
        super().__init__(RateLimit, db)
        self.tier_crud: Any = FastCRUD(Tier)

    @property
    def effective_tier_crud(self) -> Any:
        """Resolve the active tier CRUD handle."""
        return self.tier_crud

    # ---------------------------------------------------------------------------
    # Tier Operations (absorbed from tiers module)
    # ---------------------------------------------------------------------------

    async def create_tier(self, tier: TierCreate) -> dict[str, Any]:
        """Create a new tier with validation.

        Parameters
        ----------
        tier : TierCreate
            Tier creation data

        Returns
        -------
        dict[str, Any]
            Created tier data

        Raises
        ------
        DuplicateError
            If tier name already exists
        ModelNotFoundError
            If tier creation fails
        """
        tier_dict = tier.model_dump()
        crud = self.effective_tier_crud
        tier_exists = await crud.exists(db=self.db, name=tier_dict["name"])
        if tier_exists:
            raise DuplicateError("name", tier_dict["name"])

        tier_internal = TierCreateInternal(**tier_dict)
        created_tier = await crud.create(db=self.db, object=tier_internal, schema_to_select=TierRead)

        if created_tier is None:
            raise ModelNotFoundError("Tier", "new")

        return cast(dict[str, Any], created_tier)

    async def get_tiers(self, page: int = 1, items_per_page: int = 10) -> dict[str, Any]:
        """Get paginated list of tiers.

        Parameters
        ----------
        page : int
            Page number
        items_per_page : int
            Items per page

        Returns
        -------
        dict[str, Any]
            Paginated tiers response
        """
        crud = self.effective_tier_crud
        tiers_data = await crud.get_multi(
            db=self.db, offset=(page - 1) * items_per_page, limit=items_per_page, schema_to_select=TierRead
        )
        response: dict[str, Any] = paginated_response(crud_data=tiers_data, page=page, items_per_page=items_per_page)
        return response

    async def get_tier_by_id(self, tier_id: int) -> dict[str, Any]:
        """Get a tier by ID.

        Parameters
        ----------
        tier_id : int
            Tier ID

        Returns
        -------
        dict[str, Any]
            Tier data

        Raises
        ------
        ModelNotFoundError
            If tier not found
        """
        crud = self.effective_tier_crud
        db_tier = await crud.get(db=self.db, id=tier_id, schema_to_select=TierRead)
        if db_tier is None:
            raise ModelNotFoundError("Tier", tier_id)
        return cast(dict[str, Any], db_tier)

    async def get_tier(self, name: str) -> dict[str, Any]:
        """Get a tier by name.

        Parameters
        ----------
        name : str
            Tier name

        Returns
        -------
        dict[str, Any]
            Tier data

        Raises
        ------
        ModelNotFoundError
            If tier not found
        """
        crud = self.effective_tier_crud
        db_tier = await crud.get(db=self.db, name=name, schema_to_select=TierRead)
        if db_tier is None:
            raise ModelNotFoundError("Tier", name)
        return cast(dict[str, Any], db_tier)

    async def update_tier(self, name: str, values: TierUpdate) -> dict[str, str]:
        """Update a tier.

        Parameters
        ----------
        name : str
            Tier name to update
        values : TierUpdate
            Update values

        Returns
        -------
        dict[str, str]
            Success message

        Raises
        ------
        ModelNotFoundError
            If tier not found
        DuplicateError
            If attempting to rename an existing tier
        """
        crud = self.effective_tier_crud
        db_tier = await crud.get(db=self.db, name=name, schema_to_select=TierRead)
        if db_tier is None:
            raise ModelNotFoundError("Tier", name)

        # B11 FIX: renames are not supported; create a new tier instead
        new_name = getattr(values, "name", None)
        if isinstance(new_name, str) and new_name and new_name != name:
            raise DuplicateError("name", f"renaming tier '{name}' is not supported; create a new tier instead")

        await crud.update(db=self.db, object=values, name=name)

        # Invalidate tier cache on mutation
        keys = [f"tier:{name}"]
        if new_name and new_name != name:
            keys.append(f"tier:{new_name}")
        await self._invalidate_tier_cache(keys, tier_name=name)

        return {"message": "Tier updated"}

    async def _invalidate_tier_cache(self, keys: list[str], tier_name: str) -> None:
        """B10: invalidate tier cache keys with retry — never fail-open.

        cache_guard's documented non-goals (core/utils/cache_guard.py) exclude
        mutation-side invalidation: a swallowed DELETE leaves every worker
        serving the old, authority-contradicting tier policy until TTL expiry
        (e.g. a lowered limit stays generous for up to 1h). Mirror the tenants
        Q-M8 posture instead: retry once, then error-log with identifiers so
        the stale-policy window is visible to operators.
        """
        redis_client = get_redis()
        if not redis_client:
            return
        for attempt in (1, 2):
            try:
                await redis_client.delete(*keys)
                return
            except Exception as e:
                if attempt == 1:
                    logger.warning(
                        "Tier cache invalidation failed (retrying)",
                        tier=tier_name,
                        keys=keys,
                        error=str(e),
                    )
                else:
                    logger.error(
                        "Tier cache invalidation failed after retry — stale tier policy may persist until TTL",
                        tier=tier_name,
                        keys=keys,
                        error=str(e),
                    )

    async def delete_tier(self, name: str) -> dict[str, str]:
        """Delete a tier with referential integrity guard.

        Parameters
        ----------
        name : str
            Tier name to delete

        Returns
        -------
        dict[str, str]
            Success message

        Raises
        ------
        ModelNotFoundError
            If tier not found
        DuplicateError
            If tier is still in use by users or rate limits (Q-M1: was
            HTTPException 409; the central handler maps DuplicateError
            to 409 with the field/value details)
        RuntimeError
            If checking referential integrity fails unexpectedly
        """
        crud = self.effective_tier_crud
        db_tier = await crud.get(db=self.db, name=name, schema_to_select=TierRead)
        if db_tier is None:
            raise ModelNotFoundError("Tier", name)

        from src.app.modules.users.models import User

        tier_id = db_tier.get("id") if isinstance(db_tier, dict) else getattr(db_tier, "id", None)

        if tier_id is not None:
            try:
                users_result = await self.db.execute(
                    select(func.count()).select_from(User).where(User.tier_id == tier_id)
                )
                users_count = int(users_result.scalar_one() or 0)
                rl_result = await self.db.execute(
                    select(func.count()).select_from(RateLimit).where(RateLimit.tier_id == tier_id)
                )
                rl_count = int(rl_result.scalar_one() or 0)
            except Exception as e:
                logger.error("failed_to_check_tier_references", tier_id=tier_id, name=name, error=str(e))
                raise RuntimeError(f"Failed to check referential integrity for tier '{name}': {e}") from e

            if users_count > 0 or rl_count > 0:
                # Q-M1: domain raise instead of HTTPException(409) — the
                # central DuplicateError handler maps this to 409.
                raise DuplicateError(
                    "tier_in_use",
                    f"'{name}' ({users_count} user(s), {rl_count} rate limit(s))",
                    message=f"Tier '{name}' is still in use ({users_count} user(s), {rl_count} rate limit(s)) "
                    "and cannot be deleted",
                )

        await crud.delete(db=self.db, name=name)

        # Invalidate tier and rate-limit caches
        keys = [f"tier:{name}"]
        if tier_id:
            keys.append(f"rate_limits:tier:{tier_id}")
        await self._invalidate_tier_cache(keys, tier_name=name)

        return {"message": "Tier deleted"}

    # ---------------------------------------------------------------------------
    # Rate Limit Operations
    # ---------------------------------------------------------------------------

    async def _get_tier_or_404(self, tier_name: str) -> dict[str, Any]:
        """Resolve a tier by name, raising ModelNotFoundError when absent.

        Q-M6: the old ``if TierService is not RateLimitService`` branch
        existed only because TierService INHERITED from this class and
        tests patched the alias to intercept. TierService now COMPOSES
        this service and delegates, so the tier resolution has exactly
        one implementation: this method.
        """
        return cast(dict[str, Any], await self.get_tier(tier_name))

    async def _invalidate_tier_rate_limits_cache(self, tier_id: int) -> None:
        """Invalidate rate limits cache for a tier (B10: retrying, never bare)."""
        await self._invalidate_tier_cache([f"rate_limits:tier:{tier_id}"], tier_name=str(tier_id))

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

        # Generate unique name if not provided
        if not rate_limit_dict.get("name"):
            clean_path = rate_limit_dict["path"].replace("/", "_").strip("_")
            rate_limit_dict["name"] = f"{tier_name}_{clean_path}_{rate_limit_dict['limit']}_{rate_limit_dict['period']}"

        # Check if rate limit name already exists
        rate_limit_exists = await self.crud.exists(db=self.db, name=rate_limit_dict["name"])
        if rate_limit_exists:
            raise DuplicateError("name", rate_limit_dict["name"])

        # P2-3: Check if rate limit for same path already exists in this tier
        path_exists = await self.crud.exists(db=self.db, tier_id=db_tier["id"], path=rate_limit_dict["path"])
        if path_exists:
            raise DuplicateError("path", f"Rate limit for path '{rate_limit_dict['path']}' already exists in this tier")

        rate_limit_internal = RateLimitCreateInternal(**rate_limit_dict)
        created_rate_limit = await self.crud.create(
            db=self.db, object=rate_limit_internal, schema_to_select=RateLimitRead
        )

        if created_rate_limit is None:
            raise ModelNotFoundError("RateLimit", "new")

        # Invalidate cache for this tier
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
        """
        db_tier = await self._get_tier_or_404(tier_name)

        offset = compute_offset(page, items_per_page)
        rate_limits_data = await self.crud.get_multi(
            db=self.db,
            offset=offset,
            limit=items_per_page,
            schema_to_select=RateLimitRead,
            tier_id=db_tier["id"],
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
            db=self.db, id=rate_limit_id, tier_id=db_tier["id"], schema_to_select=RateLimitRead
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
            db=self.db, id=rate_limit_id, tier_id=db_tier["id"], schema_to_select=RateLimitRead
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

        await self.crud.update(db=self.db, object=values, id=rate_limit_id, tier_id=db_tier["id"])

        # Invalidate cache for this tier
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
            db=self.db, id=rate_limit_id, tier_id=db_tier["id"], schema_to_select=RateLimitRead
        )

        if db_rate_limit is None:
            raise ModelNotFoundError("RateLimit", rate_limit_id)

        await self.crud.delete(db=self.db, id=rate_limit_id, tier_id=db_tier["id"])

        # Invalidate cache for this tier
        await self._invalidate_tier_rate_limits_cache(db_tier["id"])

        return {"message": "Rate Limit deleted"}


