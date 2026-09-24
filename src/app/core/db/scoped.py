"""Tenant-scoped persistence factories (see CONTEXT.md).

Two entry points over the ONE scope decision
(:func:`resolve_effective_tenant`):

- :func:`scoped_crud` — bound CRUD path (replaces direct ``TenantAwareCRUD``
  construction + per-call filter plumbing).
- :func:`execute_scoped` — hand-built ``select()`` path, scoped AND executed
  under ``db_circuit_guard`` (replaces bare ``db.execute`` + manual
  ``inject_tenant_filter`` — the combination that left the ``eager_loads``
  path unguarded).
- :func:`criterion` — migration adapter for the ``criterion()`` call sites;
  same rule as a WHERE clause. New code should prefer the factories.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

from fastcrud import FastCRUD
from sqlalchemy import true
from sqlalchemy.engine import CursorResult, Result
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement, Select

from src.app.core.db.tenant_crud import TenantAwareCRUD
from src.app.core.resilience.db_circuit_breaker import db_circuit_guard
from src.app.core.tenancy.scope import (
    ExplicitScope,
    ExplicitTenant,
    TenantIdValue,
    Unscoped,
    resolve_effective_tenant,
)


def _model_name(model: Any) -> str:
    return getattr(model, "__name__", str(model))


def _constrain(model: Any, resolved: TenantIdValue | None) -> ColumnElement[bool]:
    """Build the WHERE criterion for a resolved tenant (or no-filter tautology)."""
    if resolved is None:
        return true()
    if not hasattr(model, "tenant_id"):
        # Global tables (Tier, Tenant, ...) declare no tenant_id column, so
        # there is no predicate to apply — the query is inherently unscoped.
        # Warn (auditable) rather than refuse: refusing would break system
        # reads, and silently filtering is impossible without the column.
        from src.app.core.logger import get_logger

        get_logger(__name__).warning(
            "criterion_column_missing_allowing_global_read",
            model=_model_name(model),
        )
        return true()
    return model.tenant_id == resolved  # type: ignore[no-any-return]


CrudInstance = (
    TenantAwareCRUD[Any, Any, Any, Any, Any, Any]
    | FastCRUD[Any, Any, Any, Any, Any, Any]
)
"""Any unbound CRUD the factory hands out: tenant-aware or plain."""


_CRUD_CACHE: dict[Any, CrudInstance] = {}
"""Flyweight cache for unbound CRUD instances (moved from BaseService).

FastCRUD/TenantAwareCRUD instances are stateless (model metadata only), so
one per model is shared request-wide. Keyed by model; values are
TenantAwareCRUD for tenant-column models, plain FastCRUD otherwise.
Bound scopes (ExplicitTenant/Unscoped) are NEVER cached — they carry
caller identity. OPT P0-2 allocation behavior preserved.
"""


def _is_tenant_scoped(model: Any) -> bool:
    """True when the model declares a ``tenant_id`` column.

    Moved verbatim from ``BaseService._get_or_create_crud``: inspects actual
    table columns, not hasattr (descriptors / __getattr__ can spoof that).
    Fail-closed: introspection failure defaults to tenant-scoped.
    """
    try:
        table = getattr(model, "__table__", None)
        cols = getattr(table, "c", {}) if table is not None else {}
        return "tenant_id" in cols
    except Exception:
        return True


def scoped_crud(
    db: AsyncSession,  # noqa: ARG001 — kept for a stable factory signature; binding is dynamic per call
    model: Any,
    *,
    scope: ExplicitScope | None = None,
) -> TenantAwareCRUD[Any, Any, Any, Any, Any, Any] | FastCRUD[Any, Any, Any, Any, Any, Any]:
    """Return a tenant-scoped CRUD bound to ``scope`` (default: request context).

    The binding is dynamic — every operation resolves through
    :func:`resolve_effective_tenant`, so context-wins / fail-closed hold
    per call, not per instance. ``scope`` is fixed at bind time; pass
    ``ExplicitTenant`` for workers holding a tenant, ``Unscoped(reason=...)``
    for documented system paths.

    Unbound (``scope=None``) instances are flyweights cached per model
    (tenant-column models get ``TenantAwareCRUD``, global tables get plain
    ``FastCRUD``) — every operation still resolves the request context per
    call, so sharing is safe. Bound scopes return a fresh instance.
    """
    if scope is not None:
        # Bound to one caller (worker tenant / documented system path) —
        # carries identity, so never shared, never cached.
        return TenantAwareCRUD(model, scope=scope)
    crud = _CRUD_CACHE.get(model)
    if crud is None:
        if _is_tenant_scoped(model):
            crud = _CRUD_CACHE.setdefault(model, TenantAwareCRUD(model))  # type: ignore[arg-type]
        else:
            crud = _CRUD_CACHE.setdefault(model, FastCRUD(model))
    return crud


ScopedResult = Result[Any] | CursorResult[Any]


async def execute_scoped(
    stmt: Any,
    *,
    db: AsyncSession,
    model: Any,
    scope: ExplicitScope | None = None,
) -> ScopedResult:
    """Scope ``stmt`` to the effective tenant and execute it under the guard.

    The statement may be a SELECT, UPDATE, or DELETE. The guard applies exactly
    once here — callers must NOT wrap this in ``db_circuit_guard`` themselves.
    """
    resolved = resolve_effective_tenant(scope, model_name=_model_name(model))
    stmt = stmt.where(_constrain(model, resolved))
    async with db_circuit_guard():
        return cast(ScopedResult, await db.execute(stmt))


@asynccontextmanager
async def stream_scoped(
    stmt: Select,
    *,
    db: AsyncSession,
    model: Any,
    scope: ExplicitScope | None = None,
) -> AsyncIterator[Any]:
    """Scope and stream a SELECT while keeping the database guard active."""
    resolved = resolve_effective_tenant(scope, model_name=_model_name(model))
    stmt = stmt.where(_constrain(model, resolved))
    async with db_circuit_guard():
        result = await db.stream(stmt)
        try:
            yield result
        finally:
            await result.close()


def criterion(
    model: Any,
    tenant_id: TenantIdValue | None = None,
    *,
    scope: ExplicitScope | None = None,
    allow_unscoped: bool = False,
) -> ColumnElement[bool]:
    """Return the tenant-isolation criterion for ``model``.

    This is the ONE spelling for the tenant-filter invariant
    (``Tenant-scoped persistence``, see CONTEXT.md): context wins over the
    caller's argument, missing context is refused unless explicitly
    escaped. New code passes ``scope=`` (``ExplicitTenant`` for workers,
    ``Unscoped(reason=...)`` for documented system paths).

    ``tenant_id=`` / ``allow_unscoped=`` are the legacy migration surface
    (ex-``tenant_scope`` call sites): they map onto reason-carrying scopes
    internally (``legacy-criterion:<model>``), with the same precedence the
    old seam had — an explicit id beats the escape hatch. New code must
    NOT use them; prefer ``scope=``.
    """
    if scope is not None and (tenant_id is not None or allow_unscoped):
        raise ValueError(
            "criterion: pass scope= OR the legacy tenant_id=/allow_unscoped=, not both"
        )
    model_name = _model_name(model)
    if scope is None:
        if tenant_id is not None:
            scope = ExplicitTenant(
                tenant_id=tenant_id, reason=f"legacy-criterion:{model_name}"
            )
        elif allow_unscoped:
            scope = Unscoped(reason=f"legacy-criterion:{model_name}")
    resolved = resolve_effective_tenant(scope, model_name=model_name)
    return _constrain(model, resolved)


__all__ = ["criterion", "execute_scoped", "scoped_crud", "stream_scoped"]
