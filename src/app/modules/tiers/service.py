"""Tier service layer — canonical home of tier business logic.

P1-18: the tier operations (create/get/update/delete + cache invalidation)
moved back here verbatim from ``RateLimitService``, which now composes this
service for anything tier-shaped. Single implementation, honest domain
direction: tiers own tiers; rate limits are rules attached to tiers.
"""

from typing import Any, cast

from fastcrud import FastCRUD, paginated_response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.logger import get_logger
from src.app.core.redis_provider import get_redis
from src.app.core.schemas import MessageResponse
from src.app.modules.rate_limits.models import RateLimit
from src.app.modules.tiers.models import Tier
from src.app.modules.tiers.schemas import TierCreate, TierCreateInternal, TierRead, TierUpdate
from src.app.shared.exceptions import DuplicateError, ModelNotFoundError

logger = get_logger(__name__)


class TierService:
    """Tier domain service — the single implementation of tier operations.

    Attributes:
        crud: Tier-primary CRUD handle (BaseService[Tier]-compatible contract).
        tier_crud: Alias of ``crud`` (P0-1 decoupling regression contract).
        rate_limit_crud: RateLimit CRUD handle for direct rule queries.
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        # BaseService[Tier]-compatible crud handles (P0-1 decoupling):
        # tier operations stay tier-primary.
        self.crud: Any = FastCRUD(Tier)
        self.tier_crud: Any = self.crud
        self.rate_limit_crud: Any = FastCRUD(RateLimit)

    # ---------------------------------------------------------------------------
    # Tier Operations (canonical implementation — P1-18)
    # ---------------------------------------------------------------------------

    async def create_tier(self, tier: TierCreate) -> TierRead:
        """Create a new tier with validation.

        Parameters
        ----------
        tier : TierCreate
            Tier creation data

        Returns
        -------
        TierRead
            Created tier data

        Raises
        ------
        DuplicateError
            If tier name already exists
        ModelNotFoundError
            If tier creation fails
        """
        tier_dict = tier.model_dump()
        crud = self.tier_crud
        tier_exists = await crud.exists(db=self.db, name=tier_dict["name"])
        if tier_exists:
            raise DuplicateError("name", tier_dict["name"])

        tier_internal = TierCreateInternal(**tier_dict)
        created_tier = await crud.create(
            db=self.db, object=tier_internal, schema_to_select=TierRead, return_as_model=True
        )

        if created_tier is None:
            raise ModelNotFoundError("Tier", "new")

        return cast(TierRead, created_tier)

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
        crud = self.tier_crud
        tiers_data = await crud.get_multi(
            db=self.db, offset=(page - 1) * items_per_page, limit=items_per_page, schema_to_select=TierRead
        )
        response: dict[str, Any] = paginated_response(crud_data=tiers_data, page=page, items_per_page=items_per_page)
        return response

    async def get_tier_by_id(self, tier_id: int) -> TierRead:
        """Get a tier by ID.

        Parameters
        ----------
        tier_id : int
            Tier ID

        Returns
        -------
        TierRead
            Tier data

        Raises
        ------
        ModelNotFoundError
            If tier not found
        """
        crud = self.tier_crud
        db_tier = await crud.get(db=self.db, id=tier_id, schema_to_select=TierRead, return_as_model=True)
        if db_tier is None:
            raise ModelNotFoundError("Tier", tier_id)
        return cast(TierRead, db_tier)

    async def get_tier(self, name: str) -> TierRead:
        """Get a tier by name.

        Parameters
        ----------
        name : str
            Tier name

        Returns
        -------
        TierRead
            Tier data

        Raises
        ------
        ModelNotFoundError
            If tier not found
        """
        crud = self.tier_crud
        db_tier = await crud.get(db=self.db, name=name, schema_to_select=TierRead, return_as_model=True)
        if db_tier is None:
            raise ModelNotFoundError("Tier", name)
        return cast(TierRead, db_tier)

    async def update_tier(self, name: str, values: TierUpdate) -> MessageResponse:
        """Update a tier.

        Parameters
        ----------
        name : str
            Tier name to update
        values : TierUpdate
            Update values

        Returns
        -------
        MessageResponse
            Success message

        Raises
        ------
        ModelNotFoundError
            If tier not found
        DuplicateError
            If attempting to rename an existing tier
        """
        crud = self.tier_crud
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

        return MessageResponse(message="Tier updated")

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

    async def delete_tier(self, name: str) -> MessageResponse:
        """Delete a tier with referential integrity guard.

        Parameters
        ----------
        name : str
            Tier name to delete

        Returns
        -------
        MessageResponse
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
        crud = self.tier_crud
        db_tier = await crud.get(db=self.db, name=name, schema_to_select=TierRead, return_as_model=True)
        if db_tier is None:
            raise ModelNotFoundError("Tier", name)

        from src.app.modules.users.models import User

        # P2-C: get() with return_as_model=True always yields TierRead —
        # no dict/object dual-handling.
        tier_id = db_tier.id

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

        # P2-B: the tier is gone, so its cached policy entry
        # (rate_limit:policy:tier:{id} + L1) must go too — otherwise the
        # resolver keeps enforcing rules for a deleted tier until TTL.
        if tier_id:
            from src.app.core.security.rate_limit_cache import invalidate_tier_policy

            await invalidate_tier_policy(tier_id)

        return MessageResponse(message="Tier deleted")
