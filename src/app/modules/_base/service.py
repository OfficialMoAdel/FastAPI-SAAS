"""Base service pattern for all feature modules.

Provides a reusable base service class with common CRUD operations
using FastCRUD. All feature services should inherit from this class.

Usage:
    from src.app.modules._base import BaseService
    from src.app.modules.users.models import User

    class UserService(BaseService[User]):
        def __init__(self, db: AsyncSession):
            super().__init__(User, db)

        async def get_by_email(self, email: str) -> User | None:
            return await self.crud.get(db=self.db, email=email)

    # Dependency injection factory
    def get_user_service(db: AsyncSession = Depends(async_get_db)) -> UserService:
        return UserService(db)
"""

from __future__ import annotations

from typing import Any, Callable, Generic, TypeVar, cast

from fastcrud import FastCRUD
from sqlalchemy.ext.asyncio import AsyncSession

T = TypeVar("T")

# OPT P0-2: Flyweight cache for FastCRUD instances
# FastCRUD is stateless (stores only model metadata), so we can reuse instances
# This reduces O(N) allocations per request to O(1) for cached models
_CRUD_CACHE: dict[type, FastCRUD] = {}


class BaseService(Generic[T]):
    """
    Standardized business logic layer for all modules.

    The BaseService provides a unified interface for CRUD operations,
    integrating logging, exception handling, and standard lifecycle patterns.
    It acts as the orchestrator between routers and the data access layer (FastCRUD).

    Attributes:
        model: The SQLAlchemy model class.
        db: The async database session.
        crud: An instance of FastCRUD configured for the model.
    """

    def __init__(self, model: type[T], db: AsyncSession):
        """Initialize service with model and database session.

        Args:
            model: The SQLAlchemy model class.
            db: The async database session.
        """
        self.model = model
        self.db = db
        # OPT P0-2: Reuse cached FastCRUD instance (flyweight pattern)
        if model not in _CRUD_CACHE:
            _CRUD_CACHE[model] = FastCRUD(model)
        self.crud = _CRUD_CACHE[model]

    async def get(self, id: Any) -> T | None:
        """Get a single record by ID.

        Args:
            id: The record ID.

        Returns:
            The record if found, None otherwise.
        """
        result = await self.crud.get(db=self.db, id=id)
        return cast(T | None, result)

    async def get_multi(
        self,
        page: int = 1,
        per_page: int = 20,
        **kwargs: Any,
    ) -> list[T]:
        """Get multiple records with pagination.

        Args:
            page: Page number (1-indexed).
            per_page: Number of items per page.
            **kwargs: Additional filter parameters passed to the CRUD layer.

        Returns:
            List of records for the requested page. Returns empty list if no
            records found. FastCRUD's dict response is unwrapped automatically.
        """
        offset = (page - 1) * per_page
        result = await self.crud.get_multi(
            db=self.db,
            offset=offset,
            limit=per_page,
            **kwargs,
        )
        # FastCRUD returns dict with 'data' key containing the records list
        if isinstance(result, dict):
            return cast(list[T], result.get("data", []))
        return cast(list[T], result)

    async def create(self, **kwargs: Any) -> T:
        """Create a new record.

        Args:
            **kwargs: Fields for the new record.

        Returns:
            The created record.
        """
        result = await self.crud.create(db=self.db, object=kwargs)
        return cast(T, result)

    async def update(self, id: Any, **kwargs: Any) -> T | None:
        """Update a record by ID.

        Args:
            id: The record ID.
            **kwargs: Fields to update.

        Returns:
            The updated record if found, None otherwise.
        """
        result = await self.crud.update(db=self.db, object=kwargs, id=id)
        return cast(T | None, result)

    async def delete(self, id: Any) -> None:
        """Delete a record by ID.

        Args:
            id: The record ID.
        """
        await self.crud.delete(db=self.db, id=id)


def get_base_service(model: type[T]) -> Callable[[AsyncSession], BaseService[T]]:
    """Factory function for creating service instances via DI.

    Args:
        model: The SQLAlchemy model class.

    Returns:
        A factory function that accepts db session and returns service.

    Usage:
        from fastapi import Depends
        from src.app.core.db.database import async_get_db

        get_user_service = get_base_service(User)

        @router.get("/users/{id}")
        async def get_user(
            id: int,
            service: BaseService[User] = Depends(get_user_service)
        ):
            return await service.get(id)
    """

    def factory(db: AsyncSession) -> BaseService[T]:
        return BaseService(model, db)

    return factory


__all__ = ["BaseService", "get_base_service", "_CRUD_CACHE"]
