from src.app.core.tenancy.context import TenantContext, TenantContextError, get_current_tenant


def tenant_dependency() -> TenantContext:
    """Get the current tenant from context (FastAPI dependency).

    This dependency retrieves the tenant that was set by TenantMiddleware.
    Should only be used on endpoints that require tenant context (non-whitelisted).

    Named ``tenant_dependency`` (not ``get_current_tenant``) so it no longer
    collides with the canonical context accessor of the same name in
    ``src.app.core.tenancy.context`` — callers can now import both without
    ``import … as …`` gymnastics.

    P3-#14 (one no-tenant contract): a *missing* context (``None`` —
    middleware never ran) is a 403 ``TenantContextError`` handled by the
    central ``EXCEPTION_HANDLERS`` table — never a raw ``HTTPException``
    500. But the ``NO_TENANT`` exempt sentinel passes through: the
    middleware sets it deliberately on exempt paths (e.g. ``/api/v1/tenants``
    bootstrapping) so superuser flows work without an active tenant, and
    endpoints that need a real tenant opt into the fail-closed check via
    ``RequestContext.require_tenant()`` or ``require_active_tenant()``
    (which rejects the sentinel). Two levels, one exception class.

    Returns
    -------
        The current tenant context (or the ``NO_TENANT`` sentinel on
        exempt paths).

    Raises
    ------
        TenantContextError: If no tenant context was set at all.
    """
    tenant = get_current_tenant()
    if tenant is None:
        raise TenantContextError("No tenant context available")
    return tenant
