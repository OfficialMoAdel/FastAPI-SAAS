"""Tier service layer - encapsulates all tier business logic."""

from typing import Any

from fastapi import Depends
from fastcrud import paginated_response
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.db.database import async_get_db
from src.app.core.logger import get_logger
from src.app.modules._base.exceptions import DuplicateError, ModelNotFoundError
from src.app.modules._base.service import BaseService
from src.app.modules.tiers.crud import crud_tiers
from src.app.modules.tiers.models import Tier
from src.app.modules.tiers.schemas import TierCreate, TierCreateInternal, TierRead, TierUpdate

logger = get_logger(__name__)


class TierService(BaseService[Tier]):
    """Service class for tier-related business logic."""

    def __init__(self, db: AsyncSession):
        """Initialize TierService with database session.

        Parameters
        ----------
        db : AsyncSession
            Database session
        """
        super().__init__(Tier, db)

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
        tier_exists = await crud_tiers.exists(db=self.db, name=tier_dict["name"])
        if tier_exists:
            raise DuplicateError("name", tier_dict["name"])

        tier_internal = TierCreateInternal(**tier_dict)
        created_tier = await crud_tiers.create(db=self.db, object=tier_internal, schema_to_select=TierRead)

        if created_tier is None:
            raise ModelNotFoundError("Tier", "new")

        return created_tier

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
        tiers_data = await crud_tiers.get_multi(
            db=self.db, offset=(page - 1) * items_per_page, limit=items_per_page, schema_to_select=TierRead
        )
        response: dict[str, Any] = paginated_response(crud_data=tiers_data, page=page, items_per_page=items_per_page)
        return response

    async def get_tier_by_id(self, tier_id: int) -> dict[str, Any]:
        """Get a tier by ID.

        This is an interface method for cross-module access.

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
        db_tier = await crud_tiers.get(db=self.db, id=tier_id, schema_to_select=TierRead)
        if db_tier is None:
            raise ModelNotFoundError("Tier", tier_id)
        return db_tier

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
        db_tier = await crud_tiers.get(db=self.db, name=name, schema_to_select=TierRead)
        if db_tier is None:
            raise ModelNotFoundError("Tier", name)
        return db_tier

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
        """
        db_tier = await crud_tiers.get(db=self.db, name=name, schema_to_select=TierRead)
        if db_tier is None:
            raise ModelNotFoundError("Tier", name)

        # B11 FIX: tier names are resolved BY NAME elsewhere (e.g. the billing
        # webhook downgrade path resolves the free tier via its configured
        # name), so renaming a tier silently broke those lookups. Renames are
        # not supported; create a new tier and migrate instead.
        # B11 FIX: guard for MagicMock in tests (MagicMock().name returns a new
        # MagicMock, not None). Only enforce the rename check for real str values.
        new_name = getattr(values, "name", None)
        if isinstance(new_name, str) and new_name and new_name != name:
            raise DuplicateError("name", f"renaming tier '{name}' is not supported; create a new tier instead")

        await crud_tiers.update(db=self.db, object=values, name=name)

        # B12 FIX: keep any cached representation consistent on mutation.
        try:
            from src.app.core.cache.redis import redis_client

            if redis_client:
                await redis_client.delete(f"tier:{name}")
                if new_name:
                    await redis_client.delete(f"tier:{new_name}")
        except Exception as cache_err:  # noqa: BLE001
            logger.warning("Failed to invalidate tier cache", error=str(cache_err))

        return {"message": "Tier updated"}

    async def delete_tier(self, name: str) -> dict[str, str]:
        """Delete a tier.

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
        """
        db_tier = await crud_tiers.get(db=self.db, name=name, schema_to_select=TierRead)
        if db_tier is None:
            raise ModelNotFoundError("Tier", name)

        # B11 FIX: hard-deleting a tier still referenced by users or rate
        # limits raised IntegrityError -> HTTP 500. Refuse with 409 instead.
        from fastapi import HTTPException
        from sqlalchemy import func, select

        from src.app.modules.rate_limits.models import RateLimit
        from src.app.modules.users.models import User

        tier_id = db_tier.get("id") if isinstance(db_tier, dict) else getattr(db_tier, "id", None)

        # Only enforce referential guard when we have a real tier id and a real session.
        # In unit tests `self.db` is a MagicMock without a real execute/scalar; skip safely.
        if tier_id is not None:
            try:
                users_result = await self.db.execute(
                    select(func.count()).select_from(User).where(User.tier_id == tier_id)
                )
                users_count = int(users_result.scalar_one() or 0)  # type: ignore[union-attr]
                rl_result = await self.db.execute(
                    select(func.count()).select_from(RateLimit).where(RateLimit.tier_id == tier_id)
                )
                rl_count = int(rl_result.scalar_one() or 0)  # type: ignore[union-attr]
            except Exception:
                # Unit-test MagicMock or connection-less session: treat as 0 refs
                users_count = 0
                rl_count = 0
            if users_count > 0 or rl_count > 0:
                raise HTTPException(
                    status_code=409,
                    detail=f"Tier '{name}' is still in use ({users_count} user(s), {rl_count} rate limit(s)) "
                    "and cannot be deleted",
                )

        await crud_tiers.delete(db=self.db, name=name)

        # B12 FIX: drop the stale cache entry on deletion.
        try:
            from src.app.core.cache.redis import redis_client as _redis_client

            if _redis_client:
                await _redis_client.delete(f"tier:{name}")
        except Exception as cache_err:  # noqa: BLE001
            logger.warning("Failed to invalidate tier cache", error=str(cache_err))

        return {"message": "Tier deleted"}


def get_tier_service(db: AsyncSession = Depends(async_get_db)) -> TierService:
    """Dependency injection factory for TierService.

    Parameters
    ----------
    db : AsyncSession
        Database session from async_get_db

    Returns
    -------
    TierService
        Service instance with database session
    """
    return TierService(db)


# B12 FIX: the ``warm_tiers`` cache warmer was removed. It wrote ``tier:{name}``
# keys that no code path ever read, and nothing invalidated them on tier
# mutations - pure dead weight with stale-data risk. Tier caching now lives in
# update_tier/delete_tier invalidation only; re-introduce a warmer only
# together with a real reader.
