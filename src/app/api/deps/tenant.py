from fastapi import HTTPException, status

from src.app.core.logger import get_logger
from src.app.core.tenancy.context import TenantContext, get_tenant_context

logger = get_logger(__name__)

def get_current_tenant() -> TenantContext:
    """Get the current tenant from context.

    This dependency retrieves the tenant that was set by TenantMiddleware.
    Should only be used on endpoints that require tenant context (non-whitelisted).

    Returns
    -------
        The current tenant context.

    Raises
    ------
        HTTPException: 500 if tenant context is not set (middleware misconfiguration).
    """
    tenant = get_tenant_context()
    if tenant is None:
        logger.error("Tenant context is None - TenantMiddleware may not be configured correctly")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error: tenant context not available",
        )
    return tenant
