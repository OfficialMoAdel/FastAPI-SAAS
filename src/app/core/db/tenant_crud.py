"""Tenant-aware CRUD base class for automatic tenant isolation.

This module provides a TenantAwareCRUD class that extends FastCRUD
to automatically inject tenant_id filters into all database operations,
preventing cross-tenant data leakage.

Usage:
    from src.app.core.db.tenant_crud import TenantAwareCRUD

    class CRUDUser(TenantAwareCRUD[User, UserCreate, UserUpdate, ...]):
        pass

    crud_users = CRUDUser(User)
"""

from typing import Any, Generic, TypeVar

from fastcrud import FastCRUD
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.tenancy.context import get_tenant_context

ModelType = TypeVar("ModelType")
CreateSchemaType = TypeVar("CreateSchemaType", bound=BaseModel)
UpdateSchemaType = TypeVar("UpdateSchemaType", bound=BaseModel)
UpdateSchemaInternalType = TypeVar("UpdateSchemaInternalType", bound=BaseModel)
DeleteSchemaType = TypeVar("DeleteSchemaType", bound=BaseModel)
SelectSchemaType = TypeVar("SelectSchemaType", bound=BaseModel)


class TenantAwareCRUD(
    FastCRUD[
        ModelType,
        CreateSchemaType,
        UpdateSchemaType,
        UpdateSchemaInternalType,
        DeleteSchemaType,
        SelectSchemaType,
    ],
    Generic[
        ModelType,
        CreateSchemaType,
        UpdateSchemaType,
        UpdateSchemaInternalType,
        DeleteSchemaType,
        SelectSchemaType,
    ],
):
    """CRUD base class that automatically filters by tenant context.

    This class wraps FastCRUD operations to automatically inject the
    current tenant_id from the request context into all queries.

    The tenant context is set by TenantMiddleware at the start of each
    request and cleared in the finally block.

    Attributes
    ----------
        model: The SQLAlchemy model class this CRUD operates on.

    Example:
        >>> from src.app.core.db.tenant_crud import TenantAwareCRUD
        >>> crud = TenantAwareCRUD(User)
        >>> # This automatically filters by current tenant
        >>> users = await crud.get_multi(db)
    """

    def _inject_tenant_filter(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Inject tenant_id from context if not explicitly provided.

        Args:
            kwargs: The keyword arguments to inject tenant_id into.

        Returns
        -------
            The kwargs dict with tenant_id added if context is available.
        """
        if "tenant_id" not in kwargs:
            tenant = get_tenant_context()
            if tenant is not None:
                kwargs["tenant_id"] = tenant.id
        return kwargs

    async def get(self, db: AsyncSession, **kwargs: Any) -> dict | None:  # type: ignore[override]
        """Get a single record, filtered by tenant context.

        Args:
            db: The database session.
            **kwargs: Filter criteria for the query.

        Returns
        -------
            The record as a dict if found, None otherwise.
        """
        kwargs = self._inject_tenant_filter(kwargs)
        return await super().get(db, **kwargs)

    async def get_multi(self, db: AsyncSession, **kwargs: Any) -> dict:  # type: ignore[override]
        """Get multiple records, filtered by tenant context.

        Args:
            db: The database session.
            **kwargs: Filter and pagination criteria.

        Returns
        -------
            A dict containing the records and pagination info.
        """
        kwargs = self._inject_tenant_filter(kwargs)
        return await super().get_multi(db, **kwargs)

    async def exists(self, db: AsyncSession, **kwargs: Any) -> bool:
        """Check if a record exists, filtered by tenant context.

        Args:
            db: The database session.
            **kwargs: Filter criteria for the query.

        Returns
        -------
            True if a matching record exists, False otherwise.
        """
        kwargs = self._inject_tenant_filter(kwargs)
        return await super().exists(db, **kwargs)

    async def count(self, db: AsyncSession, **kwargs: Any) -> int:  # type: ignore[override]
        """Count records, filtered by tenant context.

        Args:
            db: The database session.
            **kwargs: Filter criteria for the query.

        Returns
        -------
            The count of matching records.
        """
        kwargs = self._inject_tenant_filter(kwargs)
        return await super().count(db, **kwargs)

    async def update(self, db: AsyncSession, object: UpdateSchemaType | dict[str, Any], **kwargs: Any) -> None:
        """Update records, filtered by tenant context.

        Args:
            db: The database session.
            object: The update data.
            **kwargs: Filter criteria for the update.
        """
        kwargs = self._inject_tenant_filter(kwargs)
        return await super().update(db, object, **kwargs)

    async def delete(self, db: AsyncSession, **kwargs: Any) -> None:  # type: ignore[override]
        """Soft delete records, filtered by tenant context.

        Args:
            db: The database session.
            **kwargs: Filter criteria for the delete.
        """
        kwargs = self._inject_tenant_filter(kwargs)
        return await super().delete(db, **kwargs)

    async def db_delete(self, db: AsyncSession, **kwargs: Any) -> None:  # type: ignore[override]
        """Hard delete records, filtered by tenant context.

        Args:
            db: The database session.
            **kwargs: Filter criteria for the delete.
        """
        kwargs = self._inject_tenant_filter(kwargs)
        return await super().db_delete(db, **kwargs)
