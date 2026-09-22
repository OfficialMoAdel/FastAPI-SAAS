"""Tenancy query helpers.

The tenant-filter invariant lives in :func:`core.db.scoped.criterion`
(``Tenant-scoped persistence``, see CONTEXT.md). This module keeps only
the context-resolution helper consumed by the seam
(:func:`core.tenancy.scope.resolve_effective_tenant`).
"""

from uuid import UUID

from src.app.core.tenancy.context import get_current_tenant


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
