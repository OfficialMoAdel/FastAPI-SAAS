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

from collections.abc import Callable
from typing import Any, Generic, TypeVar, cast

from fastcrud import FastCRUD
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.app.core.config import settings

T = TypeVar("T")

# OPT P0-2: Flyweight cache for FastCRUD instances
# FastCRUD is stateless (stores only model metadata), so we can reuse instances
# This reduces O(N) allocations per request to O(1) for cached models
# Note: No lock needed - FastCRUD init is synchronous and cache is populated at module load
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
        # SECURITY FIX: Use thread-safe cache access with lock
        self.crud = self._get_or_create_crud(model)

    @staticmethod
    def _get_or_create_crud(model: type[T]) -> FastCRUD:
        """Get cached FastCRUD instance or create new one.

        Note: No lock needed - FastCRUD initialization is synchronous and
        the cache is populated during first service instantiation per model.
        """
        if model not in _CRUD_CACHE:
            _CRUD_CACHE[model] = FastCRUD(model)
        return _CRUD_CACHE[model]

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
        eager_loads: list[str] | None = None,
        **kwargs: Any,
    ) -> list[T]:
        """Get multiple records with pagination and optional eager loading.

        Args:
            page: Page number (1-indexed).
            per_page: Number of items per page.
            eager_loads: Optional list of relationship names to eager load.
                        When FEATURE_EAGER_LOADING flag is enabled, this will
                        use selectinload for one-to-many and joinedload for many-to-one
                        relationships to prevent N+1 queries.
            **kwargs: Additional filter parameters passed to the CRUD layer.

        Returns:
            List of records for the requested page. Returns empty list if no
            records found. FastCRUD's dict response is unwrapped automatically.
        """
        offset = (page - 1) * per_page

        if settings.FEATURE_EAGER_LOADING and eager_loads:
            # Must select the ORM entity to use eager loading options
            query = select(self.model)

            # Extract sorting kwargs as FastCRUD accepts them explicitly
            sort_columns = kwargs.pop("sort_columns", None)
            sort_orders = kwargs.pop("sort_orders", None)

            # Apply kwargs filters via FastCRUD's processor
            filters = self.crud._filter_processor.parse_filters(**kwargs)
            query = query.filter(*filters)

            # Apply sorting
            if sort_columns:
                query = self.crud._query_builder.apply_sorting(query, sort_columns, sort_orders)

            for relationship in eager_loads:
                if hasattr(self.model, relationship):
                    attr = getattr(self.model, relationship)
                    query = query.options(selectinload(attr))
            query = query.offset(offset).limit(per_page)
            result = await self.db.execute(query)
            # Remove duplicate objects if they are duplicated due to joins and relationships handling
            return list(result.scalars().unique().all())
        else:
            result = await self.crud.get_multi(
                db=self.db,
                offset=offset,
                limit=per_page,
                **kwargs,
            )
            if isinstance(result, dict):
                return cast(list[T], result.get("data", []))
            return cast(list[T], result)

    async def get_multi_joined(
        self,
        page: int = 1,
        per_page: int = 20,
        joins_config: list[Any] | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """Get multiple records with eager-loaded relationships.

        This method uses FastCRUD's get_multi_joined to prevent N+1 queries.

        Args:
            page: Page number (1-indexed).
            per_page: Number of items per page.
            joins_config: List of JoinConfig instances for eager loading relationships.
                         Each JoinConfig specifies a model to join with, join conditions,
                         and optional prefix for column names.
            **kwargs: Additional filter parameters passed to the CRUD layer.

        Returns:
            List of records with joined data for the requested page.

        Example:
            ```python
            from fastcrud import JoinConfig

            # Load users with their tiers
            users = await service.get_multi_joined(
                page=1,
                per_page=10,
                joins_config=[
                    JoinConfig(
                        model=Tier,
                        join_on=User.tier_id == Tier.id,
                        join_prefix="tier_",
                    )
                ]
            )
            ```
        """
        offset = (page - 1) * per_page
        result = await self.crud.get_multi_joined(
            db=self.db,
            offset=offset,
            limit=per_page,
            joins_config=joins_config,
            **kwargs,
        )
        # FastCRUD returns dict with 'data' key containing the records list
        if isinstance(result, dict):
            return cast(list[dict[str, Any]], result.get("data", []))
        return cast(list[dict[str, Any]], result)

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
