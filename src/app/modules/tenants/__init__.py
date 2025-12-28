"""Tenants module - multi-tenant organization management.

Components are imported lazily to avoid circular imports.
Direct imports:
    from src.app.modules.tenants.service import tenant_service
    from src.app.modules.tenants.router import router as tenants_router
    from src.app.modules.tenants.crud import crud_tenants
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
        from src.app.modules.tenants.router import router as tenants_router

        return tenants_router
    if name == "tenant_service":
        from src.app.modules.tenants.service import tenant_service

        return tenant_service
    if name == "crud_tenants":
        from src.app.modules.tenants.crud import crud_tenants

        return crud_tenants
    if name == "Tenant":
        from src.app.modules.tenants.models import Tenant

        return Tenant
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
