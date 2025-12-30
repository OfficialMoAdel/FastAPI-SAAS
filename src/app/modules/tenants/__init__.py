"""Tenants module - multi-tenant organization management.

Components are imported lazily to avoid circular imports.
Direct imports:
    from .service import tenant_service
    from .router import router as tenants_router
    from .crud import crud_tenants
"""

__all__ = [
    "tenants_router",
    "tenant_service",
    "crud_tenants",
    "Tenant",
]


def __getattr__(name: str):
    """Lazy import to avoid circular imports."""
    if name == "tenants_router":
        from .router import router as tenants_router

        return tenants_router
    if name == "tenant_service":
        from .service import tenant_service

        return tenant_service
    if name == "crud_tenants":
        from .crud import crud_tenants

        return crud_tenants
    if name == "Tenant":
        from .models import Tenant

        return Tenant
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

