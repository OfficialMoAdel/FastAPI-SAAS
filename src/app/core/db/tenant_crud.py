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

# NOTE (P2-E): the ``type: ignore`` comments in this file are deliberate —
# FastCRUD's base methods are typed against non-tenant-generic signatures;
# these overrides narrow them for tenant-aware models and mypy cannot reconcile
# the variance without them.

import uuid
from typing import Any, Generic, TypeVar, cast
from uuid import UUID

from fastcrud import FastCRUD
from pydantic import BaseModel, ValidationError
from sqlalchemy import select, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement, Select

from src.app.core.exceptions.http_exceptions import TenantContextError  # noqa: F401 — re-exported; runtime raises
from src.app.core.resilience.db_circuit_breaker import db_circuit_guard
from src.app.core.tenancy.context import get_current_tenant

ModelType = TypeVar("ModelType")
CreateSchemaType = TypeVar("CreateSchemaType", bound=BaseModel)
UpdateSchemaType = TypeVar("UpdateSchemaType", bound=BaseModel)
UpdateSchemaInternalType = TypeVar("UpdateSchemaInternalType", bound=BaseModel)
DeleteSchemaType = TypeVar("DeleteSchemaType", bound=BaseModel)
SelectSchemaType = TypeVar("SelectSchemaType", bound=BaseModel)


def _resolve_tenant_id_from_context() -> UUID | None:
    """Resolve the current tenant_id from context, canonicalized to UUID.

    Shared security core for the tenant-seam functions. Returns the
    context tenant's id or None when no tenant is active (incl. the
    NO_TENANT sentinel, which is falsy).
    """
    tenant = get_current_tenant()
    if not tenant:
        return None
    tenant_id = tenant.id
    if isinstance(tenant_id, str):
        try:
            tenant_id = uuid.UUID(tenant_id)
        except (ValueError, TypeError) as e:
            from src.app.core.logger import get_logger

            get_logger(__name__).error("invalid_tenant_uuid_in_context", tenant_id=tenant_id, exc_info=e)
            raise ValueError(f"Invalid tenant UUID in context: {tenant_id}") from e
    return tenant_id


def tenant_scope(
    model: Any,
    tenant_id: int | str | UUID | None = None,
    *,
    allow_unscoped: bool = False,
) -> ColumnElement[bool]:
    """Return the tenant-isolation criterion for ``model``.

    This is the ONE interface for the tenant-filter invariant
    (architecture-review candidate 1). Every hand-rolled
    ``M.tenant_id == tid`` across services composes onto this function;
    the invariant — context wins over the caller's argument, missing
    context rejected unless explicitly escaped — lives here instead of
    being re-implemented per call site.

    Security semantics mirror :meth:`TenantAwareCRUD.inject_tenant_filter`:

    - If a tenant context is active, it ALWAYS wins over ``tenant_id``;
      a mismatching caller argument is logged as a blocked override
      (M-4: callers cannot bypass tenant isolation by passing a value).
    - If no context is active, a caller-supplied ``tenant_id`` may be used
      — the caller takes responsibility for scoping (worker paths and
      services that hold an explicit tenant use this). Passed through
      verbatim: canonicalizing here would crash callers whose tenant_id
      is not a UUID-typed value.
    - Neither context nor argument present: raises ``TenantContextError``
      unless ``allow_unscoped=True`` (the explicit, logged escape hatch,
      which returns None so the caller adds no filter).

    Args:
        model: SQLAlchemy model class with a ``tenant_id`` column.
        tenant_id: Optional explicit tenant scope. Ignored (with a
            warning) when a context tenant is active.
        allow_unscoped: Explicit escape hatch for documented system
            contexts (RLS bypass, recovery crons). Logged when used.

    Returns:
        The ``model.tenant_id == <resolved>`` criterion. Under the escape
        hatch, a ``true()`` tautology — no tenant filter, and safe to chain
        into ``.where(...)`` unconditionally.

    Raises:
        TenantContextError: No tenant in context, no ``tenant_id``
            argument, and no escape hatch.
    """
    from src.app.core.logger import get_logger

    context_id = _resolve_tenant_id_from_context()
    if context_id is not None:
        if tenant_id is not None and str(tenant_id) != str(context_id):
            get_logger(__name__).warning(
                "tenant_id_override_blocked",
                requested=str(tenant_id),
                context=str(context_id),
            )
        return cast(ColumnElement[bool], model.tenant_id == context_id)

    if tenant_id is not None:
        return cast(ColumnElement[bool], model.tenant_id == tenant_id)

    if allow_unscoped:
        get_logger(__name__).warning("tenant_context_missing_allowed_via_flag", model=getattr(model, "__name__", model))
        # true() — not None: a caller chaining .where(tenant_scope(...)) without
        # a None-check gets an explicit tautology (no filter) rather than
        # SQLAlchemy's WHERE-NULL object, which matches zero rows.
        return true()

    get_logger(__name__).error("tenant_context_missing_rejected", model=getattr(model, "__name__", model))
    raise TenantContextError(
        f"Tenant-scoped query on {getattr(model, '__name__', model)} without active tenant context"
    )


def scoped_query(
    model: type,
    tenant_id: str | UUID | None = None,
    *,
    allow_unscoped: bool = False,
) -> Select:
    """Return a tenant-scoped ``Select`` for ``model``.

    Sugar over :func:`tenant_scope` for plain selects: the criterion is
    resolved by the same rule and applied. Callers needing joins, eager
    loads, or aggregates on a shared ``select(...)`` should use
    ``tenant_scope`` directly and ``.where(criterion)`` it themselves.
    """
    return select(model).where(tenant_scope(model, tenant_id, allow_unscoped=allow_unscoped))


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
        """Inject tenant_id from context.

        SECURITY FIX (M-4): Always override explicit tenant_id to prevent
        callers from bypassing tenant isolation.

        B1 FIX: the NO_TENANT sentinel (set by TenantMiddleware on exempt
        paths such as /api/v1/billing/webhook) is falsy but not None. The old
        `tenant is not None` check let it through and scoped queries to
        UUID(int=0), silently matching 0 rows for reads/updates while Stripe
        received a 200. Truthiness now rejects both None and the sentinel.

        B6-LEAK FIX: consume the escape hatch UNCONDITIONALLY. Previously it
        was only popped in the no-context branch, so when tenant context WAS
        present the flag stayed in kwargs and reached FastCRUD, whose column
        introspection rejected it (ValueError: Invalid column '_allow_unscoped').
        """
        tenant_id = _resolve_tenant_id_from_context()
        allow_unscoped = kwargs.pop("_allow_unscoped", False) is True
        if tenant_id is not None:
            if "tenant_id" in kwargs and kwargs["tenant_id"] != tenant_id:
                from src.app.core.logger import get_logger

                get_logger(__name__).warning(
                    "tenant_id_override_blocked",
                    requested=str(kwargs["tenant_id"]),
                    context=str(tenant_id),
                )
            return {"tenant_id": tenant_id, **{k: v for k, v in kwargs.items() if k != "tenant_id"}}
        else:
            from src.app.core.logger import get_logger

            _m = getattr(self, "model", None)
            _mn = getattr(_m, "__name__", type(self).__name__) if _m else type(self).__name__

            # B1/T10: caller-supplied tenant_id kwargs cannot smuggle through
            # the unscoped branch.
            if "tenant_id" in kwargs:
                get_logger(__name__).warning(
                    "tenant_id_kwarg_ignored_without_context",
                    model=_mn,
                    requested=str(kwargs.pop("tenant_id")),
                )
            if allow_unscoped:
                get_logger(__name__).warning("tenant_context_missing_allowed_via_flag", model=_mn)
                return kwargs
            get_logger(__name__).error("tenant_context_missing_rejected", model=_mn)
            raise TenantContextError(f"Tenant-scoped query on {_mn} without active tenant context")

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
        tenant = get_current_tenant()
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
            raise TenantContextError("Tenant-scoped create executed without an active tenant context")

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

        Tenant re-point fix (M7): payload tenant_id would re-parent row in SET.
        WHERE is tenant-scoped but payload still needs stripping/coercion.

        Args:
            db: The database session.
            object: The update data.
            **kwargs: Filter criteria for the update.
        """
        # M7: strip/forbid payload tenant_id rewrite
        if isinstance(object, dict) and "tenant_id" in object:
            object = {k: v for k, v in object.items() if k != "tenant_id"}
        elif isinstance(object, BaseModel) and hasattr(object, "tenant_id"):
            tenant = get_current_tenant()
            if tenant:  # B1: truthiness rejects NO_TENANT sentinel too
                try:
                    object.tenant_id = tenant.id  # type: ignore[attr-defined]
                except (AttributeError, ValueError, TypeError, ValidationError) as e:
                    # P1 FIX (code-review): fail closed — never proceed with an
                    # uncoerced payload that may still carry attacker tenant_id
                    # in the SET clause (WHERE-scoping does not protect SET).
                    raise TenantContextError(f"Cannot enforce tenant on update payload: {e}") from e
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
