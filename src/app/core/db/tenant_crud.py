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

import uuid
from typing import Any, Generic, TypeVar, cast

from fastcrud import FastCRUD
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.resilience.db_circuit_breaker import db_circuit_guard
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

    def inject_tenant_filter(self, kwargs: dict[str, Any]) -> dict[str, Any]:
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
                tenant_id = tenant.id
                # Fix for SQLite tests where cached tenant from Redis is a dictionary with string IDs
                if isinstance(tenant_id, str):
                    try:
                        tenant_id = uuid.UUID(tenant_id)
                    except (ValueError, TypeError) as e:
                        # ISSUE 11 FIX: Log invalid UUID and fail fast
                        from src.app.core.logger import get_logger

                        get_logger(__name__).error("invalid_tenant_uuid_in_context", tenant_id=tenant_id, exc_info=e)
                        raise ValueError(f"Invalid tenant UUID in context: {tenant_id}") from e
                return {"tenant_id": tenant_id, **kwargs}
            else:
                # Fail-closed: raise an error if context is missing for a tenant-aware operation
                raise RuntimeError("Tenant-scoped query executed without an active tenant context")
        return kwargs

    def _inject_tenant_filter(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Alias for inject_tenant_filter for backward compatibility."""
        return self.inject_tenant_filter(kwargs)

    async def create(self, db: AsyncSession, object: CreateSchemaType | dict[str, Any], **kwargs: Any) -> Any:
        """Create a new record, automatically injecting tenant_id from context.

        Supports all FastCRUD keyword arguments via **kwargs (e.g., commit, schema_to_select).

        Args:
            db: The database session.
            object: The creation data.
            **kwargs: Additional FastCRUD keyword arguments.

        Returns
        -------
            The created model instance or selected schema.
        """
        tenant = get_tenant_context()
        if tenant:
            tenant_id = tenant.id
            # Fix for SQLite tests where cached tenant from Redis is a dictionary with string IDs
            if isinstance(tenant_id, str):
                try:
                    tenant_id = uuid.UUID(tenant_id)
                except (ValueError, TypeError) as exc:
                    raise ValueError(f"Invalid tenant_id format: {tenant_id!r}. Must be a valid UUID.") from exc

            # Inject tenant_id if not already present, or overwrite if maliciously provided
            if isinstance(object, dict):
                # Ensure we don't mutate the original dictionary
                object = dict(object)
                object["tenant_id"] = tenant_id
                # FastCRUD.create requires a Pydantic model, not a raw dict.
                # Convert the dict to the configured CreateSchemaType so the
                # documented `object: CreateSchemaType | dict` contract holds.
                create_schema = getattr(self, "create_schema", None)
                if create_schema is None:
                    raise TypeError(
                        f"{type(self).__name__} received a dict for `create` but has "
                        "no `create_schema` configured. Pass a Pydantic model instead, "
                        "or set `create_schema` on the CRUD class."
                    )
                object = create_schema(**object)
            elif isinstance(object, BaseModel):
                # Forcibly overwrite tenant_id to prevent cross-tenant data creation
                if hasattr(object, "tenant_id"):
                    object.tenant_id = tenant_id
        else:
            raise RuntimeError("Tenant-scoped create executed without an active tenant context")

        async with db_circuit_guard():
            return await super().create(db, object, **kwargs)  # type: ignore[arg-type]

    async def get(self, db: AsyncSession, **kwargs: Any) -> dict | None:  # type: ignore[override]
        """Get a single record, filtered by tenant context.

        Args:
            db: The database session.
            **kwargs: Filter criteria for the query.

        Returns
        -------
            The record as a dict if found, None otherwise.
        """
        kwargs = self.inject_tenant_filter(kwargs)
        async with db_circuit_guard():
            return cast(dict[Any, Any] | None, await super().get(db, **kwargs))

    async def get_multi(self, db: AsyncSession, **kwargs: Any) -> dict:
        """Get multiple records, filtered by tenant context.

        Args:
            db: The database session.
            **kwargs: Filter and pagination criteria.

        Returns
        -------
            A dict containing the records and pagination info.
        """
        kwargs = self.inject_tenant_filter(kwargs)
        async with db_circuit_guard():
            return cast(dict[Any, Any], await super().get_multi(db, **kwargs))

    async def get_joined(self, db: AsyncSession, **kwargs: Any) -> dict | None:  # type: ignore[override]
        """Get a single record with joins, filtered by tenant context."""
        kwargs = self.inject_tenant_filter(kwargs)
        async with db_circuit_guard():
            return cast(dict[Any, Any] | None, await super().get_joined(db, **kwargs))

    async def get_multi_joined(self, db: AsyncSession, **kwargs: Any) -> dict:  # type: ignore[override]
        """Get multiple records with joins, filtered by tenant context."""
        kwargs = self.inject_tenant_filter(kwargs)
        async with db_circuit_guard():
            return cast(dict[Any, Any], await super().get_multi_joined(db, **kwargs))

    async def exists(self, db: AsyncSession, **kwargs: Any) -> bool:
        """Check if a record exists, filtered by tenant context.

        Args:
            db: The database session.
            **kwargs: Filter criteria for the query.

        Returns
        -------
            True if a matching record exists, False otherwise.
        """
        kwargs = self.inject_tenant_filter(kwargs)
        async with db_circuit_guard():
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
        kwargs = self.inject_tenant_filter(kwargs)
        async with db_circuit_guard():
            return await super().count(db, **kwargs)

    async def update(self, db: AsyncSession, object: UpdateSchemaType | dict[str, Any], **kwargs: Any) -> None:
        """Update records, filtered by tenant context.

        Args:
            db: The database session.
            object: The update data.
            **kwargs: Filter criteria for the update.
        """
        kwargs = self.inject_tenant_filter(kwargs)
        async with db_circuit_guard():
            await super().update(db, object, **kwargs)

    async def delete(self, db: AsyncSession, **kwargs: Any) -> None:  # type: ignore[override]
        """Soft delete records, filtered by tenant context.

        Args:
            db: The database session.
            **kwargs: Filter criteria for the delete.
        """
        kwargs = self.inject_tenant_filter(kwargs)
        async with db_circuit_guard():
            await super().delete(db, **kwargs)

    async def db_delete(self, db: AsyncSession, **kwargs: Any) -> None:  # type: ignore[override]
        """Hard delete records, filtered by tenant context.

        Args:
            db: The database session.
            **kwargs: Filter criteria for the delete.
        """
        kwargs = self.inject_tenant_filter(kwargs)
        async with db_circuit_guard():
            await super().db_delete(db, **kwargs)
