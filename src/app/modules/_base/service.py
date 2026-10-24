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

    # Q-M4: dependency factories (get_*_service with Depends(...)) live in
    # router modules, NOT in service files — service files stay
    # framework-free. Register the factory in the router that consumes it:

    # router.py:
    # def get_user_service(db: AsyncSession = Depends(async_get_db)) -> UserService:
    #     return UserService(db)
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any, Generic, TypeVar, cast

from fastcrud import FastCRUD
from fastcrud.crud.validation import validate_pagination_params
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.app.core.db.scoped import _CRUD_CACHE, execute_scoped, scoped_crud
from src.app.core.db.tenant_crud import TenantAwareCRUD
from src.app.core.pagination import MAX_PER_PAGE
from src.app.shared.exceptions import ValidationError

T = TypeVar("T")

# io15.2: the read/response schema this service hands back.
#
# ``select_schema`` is a RUNTIME attribute holding a schema class, so the CRUD
# return value cannot be typed as ``T`` (the model): it is an instance of
# whatever schema was configured, or the legacy FastCRUD dict when none was.
# Typing it as ``T`` would be a lie; leaving it ``Any`` is what let a caller
# treat a legitimate ``None`` as a value -- the defect class behind the P0-4
# near-miss in tenant_crud.
#
# A second TypeVar with a DEFAULT (PEP 696) is the mechanism: the 18 existing
# ``BaseService[Model]`` subclasses keep working untouched, and a service that
# wants real typing opts in by naming its read schema:
#
#     class UserService(BaseService[User, UserRead]): ...
#
# Undeclared, it stays ``Any`` and behaves exactly as before, so this is
# additive -- not a refactor of every service.
ReadSchemaT = TypeVar("ReadSchemaT", default=Any)

# OPT P0-2: the flyweight cache lives in core.db.scoped (owned by scoped_crud,
# the single construction path). _CRUD_CACHE is re-exported here — same dict
# object — for the cache tests and the R4 perf gate importing it from this module.


class BaseService(Generic[T, ReadSchemaT]):
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

    def __init__(
        self,
        model: type[T],
        db: AsyncSession,
        *,
        select_schema: Any | None = None,
    ):
        """Initialize service with model, database session, and response schema.

        Args:
            model: The SQLAlchemy model class.
            db: The async database session.
            select_schema: Pydantic Read schema returned by ``get``/``get_multi``/
                ``create``/``update``. When set, these methods return validated
                model instances instead of FastCRUD's raw dicts — the
                dict-vs-model question lives here, once, not at every caller.
                Subclasses without a Read schema keep the legacy dict behavior
                (returns then are ``T | dict``; narrow at the call site).
        """
        self.model = model
        self.db = db
        self.select_schema = select_schema
        # Single construction path (Phase 3): scoped_crud owns the
        # tenant-vs-plain choice (cached flyweight) and the scope binding.
        # The guard decision is read back ONCE here, next to the creation —
        # never sniffed per call (the old _circuit_guard isinstance dance).
        self.crud = scoped_crud(db=db, model=model)
        self._needs_guard = not isinstance(self.crud, TenantAwareCRUD)

    @asynccontextmanager
    async def _guard(self) -> AsyncIterator[None]:
        """Yield bare for tenant CRUD, guarded for plain FastCRUD.

        TenantAwareCRUD applies db_circuit_guard internally per method;
        global-table CRUDs (plain FastCRUD — Tier, Tenant, ...) need the
        guard here. The branch is fixed at construction (self._needs_guard).
        """
        if self._needs_guard:
            from src.app.core.resilience.db_circuit_breaker import db_circuit_guard

            async with db_circuit_guard():
                yield
        else:
            yield


    async def _read(self, id: Any) -> ReadSchemaT | dict[str, Any] | None:
        """Read one record WITHOUT opening the circuit-breaker guard.

        The caller owns the guard. ``update`` needs to re-read inside the
        same guarded unit as its write, and nesting ``get`` (which opens its
        own guard) would claim the HALF_OPEN probe slot twice — the second
        claim is refused, so a half-open database would fail the update with
        a 503 instead of letting the single probe through.
        """
        if self.select_schema is not None:
            result = await self.crud.get(
                db=self.db,
                id=id,
                schema_to_select=self.select_schema,
                return_as_model=True,
            )
        else:
            result = await self.crud.get(db=self.db, id=id)
        # FastCRUD is the untyped boundary; the union above is what we can
        # actually promise its return maps onto.
        return cast("ReadSchemaT | dict[str, Any] | None", result)

    async def get(self, id: Any) -> ReadSchemaT | dict[str, Any] | None:
        """Get a single record by ID as the configured ``select_schema``.

        Args:
            id: The record ID.

        Returns:
            A validated ``select_schema`` instance when the schema is
            configured, ``None`` otherwise. Without a schema the legacy
            FastCRUD dict is returned (``T | dict | None`` — narrow at the
            call site).
        """
        async with self._guard():
            return await self._read(id)

    async def get_multi(
        self,
        page: int = 1,
        per_page: int = 20,
        eager_loads: list[str] | None = None,
        **kwargs: Any,
    ) -> list[T | dict[str, Any]]:
        """Get multiple records with pagination and optional eager loading.

        Args:
            page: Page number (1-indexed).
            per_page: Number of items per page.
            eager_loads: Optional list of relationship names to eager load.
                        Uses selectinload to eagerly fetch relationships and
                        prevent N+1 queries.
            **kwargs: Additional filter parameters passed to the CRUD layer.

        Returns:
            List of validated ``select_schema`` instances for the requested
            page (empty list if none). Without a schema the eager-loads path
            returns ORM models and the plain path returns FastCRUD dicts —
            narrow at the call site.
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
            # Defence in depth: the column list is also validated per-attribute
            # in the eager-load branch below (hasattr fails closed), but the
            # plain path hands it straight to FastCRUD. The input may legally
            # be a bare string (fetch_page_with_total declares
            # ``str | list[str] | None``); iterating a string yields CHARACTERS,
            # so a bare "hashed_password" used to slip past this guard entirely.
            _sc = kwargs.get("sort_columns")
            _cols = [_sc] if isinstance(_sc, str) else (_sc or [])
            if any(c in _SENSITIVE_COLS for c in _cols):
                raise ValidationError(f"Sorting on sensitive column denied: {_sc}")
        # P2-4: FEATURE_EAGER_LOADING flag removed; callers that want eager
        # loading must opt in via eager_loads. Callers that don't pass it
        # get a plain CRUD query (no surprise N+1 prevention, no silent
        # flag-gated log line).
        if eager_loads:
            # Must select the ORM entity to use eager loading options
            query = select(self.model)

            # Extract sorting kwargs as FastCRUD accepts them explicitly
            sort_columns = kwargs.pop("sort_columns", None)
            sort_orders = kwargs.pop("sort_orders", None)

            # M3: hasattr silently dropped typos (was if hasattr)
            for key, value in kwargs.items():
                if not hasattr(self.model, key):
                    raise ValidationError(f"Unknown filter column '{key}' on {self.model.__name__}")
                query = query.filter(getattr(self.model, key) == value)

            # Apply sorting via direct SQLAlchemy order_by
            if sort_columns:
                orders = sort_orders if sort_orders else ["asc"] * len(sort_columns)
                for col, order in zip(sort_columns, orders):
                    if not hasattr(self.model, col):
                        raise ValidationError(f"Unknown sort column '{col}' on {self.model.__name__}")
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
            # Scoping + guard come from the seam: execute_scoped resolves
            # the tenant (fail-closed without one), applies the criterion,
            # and executes under exactly one db_circuit_guard. Deliberately
            # NOT wrapped in self._guard (double-guard).
            query_result = await execute_scoped(query, db=self.db, model=self.model)
            # Remove duplicate objects if they are duplicated due to joins and relationships handling
            return list(query_result.scalars().unique().all())
        async with self._guard():
            result = await self.crud.get_multi(
                db=self.db,
                offset=offset,
                limit=per_page,
                return_total_count=False,
                schema_to_select=self.select_schema,
                return_as_model=self.select_schema is not None,
                **kwargs,
            )
            if isinstance(result, dict):
                return cast("list[T | dict[str, Any]]", result.get("data", []))
            return cast("list[T | dict[str, Any]]", result)

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
        # H-8: same page/per_page clamp as get_multi — page=0 previously
        # produced a negative OFFSET (500 on malformed pagination input).
        if page < 1:
            page = 1
        if per_page < 1:
            per_page = 1
        if per_page > MAX_PER_PAGE:
            per_page = MAX_PER_PAGE
        offset = max(0, (page - 1) * per_page)
        async with self._guard():
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

    async def create(self, **kwargs: Any) -> ReadSchemaT | dict[str, Any] | None:
        """Create a new record.

        Args:
            **kwargs: Fields for the new record.

        Returns:
            The created record as a validated ``select_schema`` instance when
            the schema is configured (FastCRUD returns it directly — no extra
            round-trip), otherwise the legacy dict/None.
        """
        async with self._guard():
            if self.select_schema is not None:
                return cast(
                    "ReadSchemaT | dict[str, Any] | None",
                    await self.crud.create(
                        db=self.db,
                        object=kwargs,
                        schema_to_select=self.select_schema,
                        return_as_model=True,
                    ),
                )
            result = await self.crud.create(db=self.db, object=kwargs)
        return cast("ReadSchemaT | dict[str, Any] | None", result)

    async def update(self, id: Any, **kwargs: Any) -> ReadSchemaT | dict[str, Any] | None:
        """Update a record by ID.

        Args:
            id: The record ID.
            **kwargs: Fields to update.

        Returns:
            The updated record as a validated ``select_schema`` instance when
            the schema is configured, otherwise the legacy FastCRUD union
            (``T | dict | None`` — narrow at the call site).

        P3-2: the row comes from the UPDATE itself (``return_as_model`` makes
        FastCRUD add ``RETURNING <all columns>``, execution.py:43-62) instead
        of a follow-up ``get``, so a typed update costs ONE statement instead
        of COUNT + UPDATE + SELECT. The pre-flight count still runs inside
        FastCRUD, so a missing row still raises ``NoResultFound`` before any
        write — ``None`` here means "no return was requested", exactly as the
        wrapper contract says, and never "no such row".
        """
        async with self._guard():
            if self.select_schema is not None:
                result = await self.crud.update(
                    db=self.db,
                    object=kwargs,
                    id=id,
                    schema_to_select=self.select_schema,
                    return_as_model=True,
                )
            else:
                result = await self.crud.update(db=self.db, object=kwargs, id=id)
        # FastCRUD is the untyped boundary; the union above is what we can
        # actually promise its return maps onto.
        return cast("ReadSchemaT | dict[str, Any] | None", result)

    async def delete(self, id: Any) -> None:
        """Delete a record by ID.

        Args:
            id: The record ID.
        """
        async with self._guard():
            await self.crud.delete(db=self.db, id=id)

    async def get_page_with_total(
        self,
        *,
        offset: int = 0,
        limit: int | None = 100,
        schema_to_select: Any | None = None,
        sort_columns: str | list[str] | None = None,
        sort_orders: str | list[str] | None = None,
        return_as_model: bool = False,
        **filters: Any,
    ) -> dict[str, Any]:
        """Single-round-trip paginated fetch on this service's model/crud."""
        return await fetch_page_with_total(
            self.crud,
            self.db,
            offset=offset,
            limit=limit,
            schema_to_select=schema_to_select,
            sort_columns=sort_columns,
            sort_orders=sort_orders,
            return_as_model=return_as_model,
            **filters,
        )


_PAGE_TOTAL_LABEL = "__page_total"


async def fetch_page_with_total(
    crud: FastCRUD,
    db: AsyncSession,
    *,
    offset: int = 0,
    limit: int | None = 100,
    schema_to_select: Any | None = None,
    sort_columns: str | list[str] | None = None,
    sort_orders: str | list[str] | None = None,
    return_as_model: bool = False,
    **filters: Any,
) -> dict[str, Any]:
    """P0-5: single-round-trip replacement for ``crud.get_multi(..., return_total_count=True)``.

    FastCRUD's ``get_multi`` issues the page SELECT and then a second
    sequential ``COUNT(*)``. This builds the *identical* filtered/sorted
    statement via ``crud.select`` and fetches page + total in ONE query with
    ``COUNT(*) OVER()`` (the window is evaluated over the filtered set before
    LIMIT/OFFSET — same proven pattern as
    ``audit_logs.query_logs_with_total``). Response shape matches
    ``get_multi`` exactly: ``{"data": [...], "total_count": N}``.

    Non-joined filters only (no ``get_multi_joined``). Tenant scoping is
    applied via ``TenantAwareCRUD.inject_tenant_filter`` exactly as
    ``get_multi`` does. Out-of-range pages pay one fallback COUNT (the total
    is unknowable from an empty window); page 0 of an empty set returns
    ``([], 0)`` with no second query. The whole fetch runs inside
    ``db_circuit_guard`` (TenantAwareCRUD callers were guarded inside
    ``get_multi`` before; plain-FastCRUD callers gain the same fail-fast
    protection).
    """
    from src.app.core.resilience import db_circuit_guard

    validate_pagination_params(offset, limit)
    # The isinstance probe is load-bearing polymorphism, not a sniff to kill:
    # test_windowed_pagination pins parity where a plain FastCRUD over a
    # tenant-column table reads UNSCOPED. The CRUD class chooses the contract.
    scoped = crud.inject_tenant_filter(dict(filters)) if isinstance(crud, TenantAwareCRUD) else dict(filters)

    async with db_circuit_guard():
        base = await crud.select(
            schema_to_select=schema_to_select,
            sort_columns=sort_columns,
            sort_orders=sort_orders,
            **scoped,
        )
        paged = base.add_columns(func.count().over().label(_PAGE_TOTAL_LABEL)).offset(offset)
        if limit is not None:
            paged = paged.limit(limit)
        rows = (await db.execute(paged)).mappings().all()
        if rows:
            total = int(rows[0][_PAGE_TOTAL_LABEL])
            data = [{k: v for k, v in dict(row).items() if k != _PAGE_TOTAL_LABEL} for row in rows]
            if return_as_model:
                if schema_to_select is None:
                    raise ValueError("schema_to_select must be provided when return_as_model is True")
                data = [schema_to_select(**row) for row in data]
            return {"data": data, "total_count": total}
        if offset == 0:
            return {"data": [], "total_count": 0}
        # Out-of-range page: the window yields no rows, so fall back to a
        # cheap COUNT over the identical filtered set (ORDER BY stripped).
        count_stmt = select(func.count()).select_from(base.order_by(None).subquery())
        total = (await db.execute(count_stmt)).scalar() or 0
        return {"data": [], "total_count": int(total)}


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


__all__ = ["BaseService", "fetch_page_with_total", "get_base_service", "_CRUD_CACHE"]
