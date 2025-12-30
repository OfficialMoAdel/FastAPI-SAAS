"""Tier service layer - encapsulates all tier business logic."""

from typing import Any

from fastcrud import paginated_response
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.exceptions.http_exceptions import DuplicateValueException, NotFoundException
from .crud import crud_tiers
from .schemas import TierCreate, TierCreateInternal, TierRead, TierUpdate


class TierService:
    """Service class for tier-related business logic."""

    async def create_tier(self, tier: TierCreate, db: AsyncSession) -> dict[str, Any]:
        """Create a new tier with validation.

        Parameters
        ----------
        tier : TierCreate
            Tier creation data
        db : AsyncSession
            Database session

        Returns
        -------
        dict[str, Any]
            Created tier data

        Raises
        ------
        DuplicateValueException
            If tier name already exists
        NotFoundException
            If tier creation fails
        """
        tier_dict = tier.model_dump()
        tier_exists = await crud_tiers.exists(db=db, name=tier_dict["name"])
        if tier_exists:
            raise DuplicateValueException("Tier Name not available")

        tier_internal = TierCreateInternal(**tier_dict)
        created_tier = await crud_tiers.create(db=db, object=tier_internal, schema_to_select=TierRead)

        if created_tier is None:
            raise NotFoundException("Failed to create tier")

        return created_tier

    async def get_tiers(self, db: AsyncSession, page: int = 1, items_per_page: int = 10) -> dict[str, Any]:
        """Get paginated list of tiers.

        Parameters
        ----------
        db : AsyncSession
            Database session
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
            db=db, offset=(page - 1) * items_per_page, limit=items_per_page
        )
        response: dict[str, Any] = paginated_response(crud_data=tiers_data, page=page, items_per_page=items_per_page)
        return response

    async def get_tier_by_id(self, tier_id: int, db: AsyncSession) -> dict[str, Any]:
        """Get a tier by ID.

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
            Tier data

        Raises
        ------
        NotFoundException
            If tier not found
        """
        db_tier = await crud_tiers.get(db=db, id=tier_id, schema_to_select=TierRead)
        if db_tier is None:
            raise NotFoundException("Tier not found")
        return db_tier


    async def get_tier(self, name: str, db: AsyncSession) -> dict[str, Any]:
        """Get a tier by name.

        Parameters
        ----------
        name : str
            Tier name
        db : AsyncSession
            Database session

        Returns
        -------
        dict[str, Any]
            Tier data

        Raises
        ------
        NotFoundException
            If tier not found
        """
        db_tier = await crud_tiers.get(db=db, name=name, schema_to_select=TierRead)
        if db_tier is None:
            raise NotFoundException("Tier not found")
        return db_tier

    async def update_tier(self, name: str, values: TierUpdate, db: AsyncSession) -> dict[str, str]:
        """Update a tier.

        Parameters
        ----------
        name : str
            Tier name to update
        values : TierUpdate
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
            If tier not found
        """
        db_tier = await crud_tiers.get(db=db, name=name, schema_to_select=TierRead)
        if db_tier is None:
            raise NotFoundException("Tier not found")

        await crud_tiers.update(db=db, object=values, name=name)
        return {"message": "Tier updated"}

    async def delete_tier(self, name: str, db: AsyncSession) -> dict[str, str]:
        """Delete a tier.

        Parameters
        ----------
        name : str
            Tier name to delete
        db : AsyncSession
            Database session

        Returns
        -------
        dict[str, str]
            Success message

        Raises
        ------
        NotFoundException
            If tier not found
        """
        db_tier = await crud_tiers.get(db=db, name=name, schema_to_select=TierRead)
        if db_tier is None:
            raise NotFoundException("Tier not found")

        await crud_tiers.delete(db=db, name=name)
        return {"message": "Tier deleted"}


# Singleton instance for import convenience
tier_service = TierService()
