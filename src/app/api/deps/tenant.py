from fastapi import HTTPException, status

from src.app.core.logger import get_logger
from src.app.core.tenancy.context import TenantContext, get_current_tenant

logger = get_logger(__name__)


def tenant_dependency() -> TenantContext:
    """Get the current tenant from context (FastAPI dependency).

    This dependency retrieves the tenant that was set by TenantMiddleware.
    Should only be used on endpoints that require tenant context (non-whitelisted).

    Named ``tenant_dependency`` (not ``get_current_tenant``) so it no longer
    collides with the canonical context accessor of the same name in
    ``src.app.core.tenancy.context`` — callers can now import both without
    ``import … as …`` gymnastics.

    Returns
    -------
        The current tenant context.

    Raises
    ------
        HTTPException: 500 if tenant context is not set (middleware misconfiguration).
    """
    tenant = get_current_tenant()
    if tenant is None:
        logger.error("Tenant context is None - TenantMiddleware may not be configured correctly")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error: tenant context not available",
        )
    return tenant
