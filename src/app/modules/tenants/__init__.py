"""Tenants module - multi-tenant organization management.

Components are imported lazily to avoid circular imports.
Direct imports:
    from .service import get_tenant_service, TenantService
    from .router import router as tenants_router
    from .crud import crud_tenants
"""

__all__ = [
    "Tenant",
    "TenantService",
    "crud_tenants",
    "get_tenant_service",
    "tenants_router",
]


def __getattr__(name: str):
    """Lazy import to avoid circular imports."""
    if name == "tenants_router":
        from .router import router as tenants_router

        return tenants_router
    if name == "get_tenant_service":
        from .service import get_tenant_service

        return get_tenant_service
    if name == "TenantService":
        from .service import TenantService

        return TenantService
    if name == "crud_tenants":
        from .crud import crud_tenants

        return crud_tenants
    if name == "Tenant":
        from .models import Tenant

        return Tenant
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
