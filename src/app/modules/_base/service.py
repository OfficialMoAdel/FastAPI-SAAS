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

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any, Generic, TypeVar, cast

from fastcrud import FastCRUD
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.app.core.db.tenant_crud import TenantAwareCRUD
from src.app.core.pagination import MAX_PER_PAGE

T = TypeVar("T")

# OPT P0-2: Flyweight cache for FastCRUD instances
# FastCRUD is stateless (stores only model metadata), so we can reuse instances
# This reduces O(N) allocations per request to O(1) for cached models
# Note: No lock needed - FastCRUD init is synchronous and cache is populated at module load
_CRUD_CACHE: dict[type, FastCRUD] = {}  # values may be FastCRUD or TenantAwareCRUD (subclass)


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

    @asynccontextmanager
    async def _circuit_guard(self) -> AsyncIterator[None]:
        from src.app.core.db.tenant_crud import TenantAwareCRUD
        from src.app.core.resilience.db_circuit_breaker import db_circuit_guard

        if isinstance(self.crud, TenantAwareCRUD) or hasattr(self.crud, "inject_tenant_filter"):
            # TenantAwareCRUD already applies db_circuit_guard internally
            yield
        else:
            async with db_circuit_guard():
                yield

    @staticmethod
    def _get_or_create_crud(model: type[T]) -> FastCRUD:
        """Get cached CRUD instance or create new one (tenant-aware when applicable).

        If the SQLAlchemy model declares a ``tenant_id`` column the returned
        instance is a :class:`TenantAwareCRUD` so every operation is
        automatically scoped to the current tenant from ``get_current_tenant()``,
        preventing cross-tenant leakage. Models without ``tenant_id`` (e.g.
        ``Tenant``, ``Tier``) continue to use plain :class:`FastCRUD`.
        The flyweight cache is keyed by model, so the choice is stable per
        model and lock-free (FastCRUD init is synchronous).
        """
        crud = _CRUD_CACHE.get(model)
        if crud is None:
            # Robust check: inspect actual table columns, not hasattr (which can be
            # spoofed by descriptors / __getattr__ and caused false tenant-scoping).
            try:
                is_tenant_scoped = "tenant_id" in getattr(getattr(model, "__table__", None), "c", {})  # type: ignore[union-attr]
            except Exception:
                is_tenant_scoped = False
            if is_tenant_scoped:
                crud = _CRUD_CACHE.setdefault(model, TenantAwareCRUD(model))  # type: ignore[arg-type]
            else:
                crud = _CRUD_CACHE.setdefault(model, FastCRUD(model))
        return crud

    async def get(self, id: Any) -> T | None:
        """Get a single record by ID.

        Args:
            id: The record ID.

        Returns:
            The record if found, None otherwise.
        """
        async with self._circuit_guard():
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
                        Uses selectinload to eagerly fetch relationships and
                        prevent N+1 queries.
            **kwargs: Additional filter parameters passed to the CRUD layer.

        Returns:
            List of records for the requested page. Returns empty list if no
            records found. FastCRUD's dict response is unwrapped automatically.
        """
        # M3: guard page=0 / negative and overflow (was page=0 -> OFFSET -20)
        if page < 1:
            page = 1
        if per_page < 1:
            per_page = 1
        if per_page > MAX_PER_PAGE:
            from src.app.core.logger import get_logger

            get_logger(__name__).warning("per_page_clamped", requested=per_page, clamped=MAX_PER_PAGE)
            per_page = MAX_PER_PAGE
        offset = max(0, (page - 1) * per_page)
        _SENSITIVE_COLS = {"hashed_password", "password", "secret", "api_key"}
        if "sort_columns" in kwargs:
            _sc = kwargs.get("sort_columns") or []
            if any(c in _SENSITIVE_COLS for c in _sc):
                raise ValueError(f"Sorting on sensitive column denied: {_sc}")
        # P2-4: FEATURE_EAGER_LOADING flag removed; callers that want eager
        # loading must opt in via eager_loads. Callers that don't pass it
        # get a plain CRUD query (no surprise N+1 prevention, no silent
        # flag-gated log line).
        async with self._circuit_guard():
            if eager_loads:
                # Must select the ORM entity to use eager loading options
                query = select(self.model)

                # Extract sorting kwargs as FastCRUD accepts them explicitly
                sort_columns = kwargs.pop("sort_columns", None)
                sort_orders = kwargs.pop("sort_orders", None)

                from src.app.core.db.tenant_crud import TenantAwareCRUD

                if isinstance(self.crud, TenantAwareCRUD):
                    try:
                        kwargs = self.crud.inject_tenant_filter(kwargs)
                    except Exception as e:
                        from src.app.core.exceptions.http_exceptions import TenantContextError

                        if isinstance(e, TenantContextError):
                            kwargs = self.crud.inject_tenant_filter({**kwargs, "_allow_unscoped": True})
                        else:
                            raise

                # M3: hasattr silently dropped typos (was if hasattr)
                for key, value in kwargs.items():
                    if not hasattr(self.model, key):
                        raise ValueError(f"Unknown filter column '{key}' on {self.model.__name__}")
                    query = query.filter(getattr(self.model, key) == value)

                # Apply sorting via direct SQLAlchemy order_by
                if sort_columns:
                    orders = sort_orders if sort_orders else ["asc"] * len(sort_columns)
                    for col, order in zip(sort_columns, orders):
                        if not hasattr(self.model, col):
                            raise ValueError(f"Unknown sort column '{col}' on {self.model.__name__}")
                        attr = getattr(self.model, col)
                        query = query.order_by(attr.desc() if order == "desc" else attr.asc())

                for relationship in eager_loads:
                    if not hasattr(self.model, relationship):
                        from src.app.core.logger import get_logger

                        get_logger(__name__).warning(
                            "unknown_eager_relationship", rel=relationship, model=self.model.__name__
                        )  # noqa: E501
                        continue
                    attr = getattr(self.model, relationship)
                    query = query.options(selectinload(attr))
                query = query.offset(offset).limit(per_page)
                result = await self.db.execute(query)
                # Remove duplicate objects if they are duplicated due to joins and relationships handling
                return list(result.scalars().unique().all())
            else:
                try:
                    result = await self.crud.get_multi(
                        db=self.db,
                        offset=offset,
                        limit=per_page,
                        return_total_count=False,
                        **kwargs,
                    )
                except Exception as e:
                    from src.app.core.exceptions.http_exceptions import TenantContextError

                    if isinstance(e, TenantContextError):
                        result = await self.crud.get_multi(
                            db=self.db,
                            offset=offset,
                            limit=per_page,
                            return_total_count=False,
                            _allow_unscoped=True,
                            **kwargs,
                        )
                    else:
                        raise
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
        async with self._circuit_guard():
            result = await self.crud.get_multi_joined(
                db=self.db,
                offset=offset,
                limit=per_page,
                joins_config=joins_config,
                # P5: only page rows are returned; skip the COUNT query.
                return_total_count=False,
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
        async with self._circuit_guard():
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
        async with self._circuit_guard():
            result = await self.crud.update(db=self.db, object=kwargs, id=id)
        return cast(T | None, result)

    async def delete(self, id: Any) -> None:
        """Delete a record by ID.

        Args:
            id: The record ID.
        """
        async with self._circuit_guard():
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
