"""Tenancy query seam — the ONE interface for tenant-filter invariants.

This module owns the tenant-filter criterion that every tenant-shaped query
in the codebase composes onto. Keeping it in ``core/tenancy`` (not ``core/db``)
closes the coupling: the tenancy module owns both the context and the query
filter, so ``core/db`` depends on ``tenancy`` and never the reverse.

Exports
-------
- :func:`tenant_scope` — the fail-closed tenant-isolation criterion
- :func:`scoped_query` — sugar for plain tenant-scoped selects
"""

from typing import Any, cast
from uuid import UUID

from sqlalchemy import select, true
from sqlalchemy.sql import ColumnElement, Select

from src.app.core.tenancy.context import TenantContextError, get_current_tenant


def _resolve_tenant_id_from_context() -> UUID | None:
    """Resolve the current tenant_id from context, canonicalized to UUID.

    Returns the active tenant's UUID, or ``None`` if no tenant context is set
    (including the falsy ``NO_TENANT`` sentinel used on exempt paths).
    """
    tenant_ctx = get_current_tenant()
    # NO_TENANT sentinel is falsy (bool=False) but not None — truthiness catches both.
    if not tenant_ctx:
        return None
    tenant_id = tenant_ctx.id
    if tenant_id is None:
        return None
    if isinstance(tenant_id, UUID):
        return tenant_id
    if isinstance(tenant_id, int):
        return UUID(int=tenant_id)
    if isinstance(tenant_id, str):
        if tenant_id.isdigit():
            return UUID(int=int(tenant_id))
        try:
            return UUID(tenant_id)
        except (ValueError, TypeError) as e:
            from src.app.core.logger import get_logger

            get_logger(__name__).error(
                "invalid_tenant_uuid_in_context", tenant_id=tenant_id, exc_info=e
            )
            raise ValueError(f"Invalid tenant UUID in context: {tenant_id}") from e
    # int / other — coerce via int / str
    try:
        return UUID(int=int(tenant_id))
    except (ValueError, TypeError) as e:
        raise ValueError(f"Invalid tenant UUID in context: {tenant_id!r}") from e


def tenant_scope(
    model: Any,
    tenant_id: int | str | UUID | None = None,
    *,
    allow_unscoped: bool = False,
) -> ColumnElement[bool]:
    """Return the tenant-isolation criterion for ``model``.

    This is the ONE interface for the tenant-filter invariant
    (architecture-review candidate 5). Every hand-rolled
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
        get_logger(__name__).warning(
            "tenant_context_missing_allowed_via_flag",
            model=getattr(model, "__name__", model),
        )
        # true() — not None: a caller chaining .where(tenant_scope(...)) without
        # a None-check gets an explicit tautology (no filter) rather than
        # SQLAlchemy's WHERE-NULL object, which matches zero rows.
        return true()

    get_logger(__name__).error(
        "tenant_context_missing_rejected",
        model=getattr(model, "__name__", model),
    )
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


__all__ = ["scoped_query", "tenant_scope"]
