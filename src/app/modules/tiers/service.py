"""Tier service layer - encapsulates all tier business logic."""

from typing import Any

from fastcrud import paginated_response
from sqlalchemy.ext.asyncio import AsyncSession

from .._base.exceptions import DuplicateError, ModelNotFoundError
from .._base.service import BaseService
from .crud import crud_tiers
from .models import Tier
from .schemas import TierCreate, TierCreateInternal, TierRead, TierUpdate


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
        tiers_data = await crud_tiers.get_multi(db=self.db, offset=(page - 1) * items_per_page, limit=items_per_page)
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

        await crud_tiers.update(db=self.db, object=values, name=name)
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

        await crud_tiers.delete(db=self.db, name=name)
        return {"message": "Tier deleted"}


def get_tier_service(db: AsyncSession) -> TierService:
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


# DI factory registration with service registry
def _register_tier_service() -> None:
    """Register TierService with the service registry."""
    from src.app.core.service_registry import register_service

    register_service("tier", lambda db: TierService(db))
