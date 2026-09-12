"""Tenants module - multi-tenant organization management.

Components are imported lazily to avoid circular imports.
Cross-module imports MUST use the package root (P1-20: ruff TID252 bans
deep `tenants.service` / `tenants.router` imports outside this package):
    from src.app.modules.tenants import TenantService, get_tenant_service
    from src.app.modules.tenants import router as tenants_router
    from src.app.modules.tenants.crud import crud_tenants
"""

from typing import TYPE_CHECKING

__all__ = [
    "Tenant",
    "TenantService",
    "crud_tenants",
    "get_tenant_service",
    "tenants_router",
]

if TYPE_CHECKING:
    # Static-only re-exports: gives mypy real classes for the lazy
    # __getattr__ facade below (P1-20). Never executed at runtime.
    from src.app.modules.tenants.crud import crud_tenants as crud_tenants
    from src.app.modules.tenants.models import Tenant as Tenant
    from src.app.modules.tenants.router import get_tenant_service as get_tenant_service
    from src.app.modules.tenants.router import router as tenants_router
    from src.app.modules.tenants.service import TenantService as TenantService


def __getattr__(name: str):
    """Lazy import to avoid circular imports."""
    if name == "tenants_router":
        from src.app.modules.tenants.router import router as tenants_router

        return tenants_router
    if name == "get_tenant_service":
        from src.app.modules.tenants.router import get_tenant_service

        return get_tenant_service
    if name == "TenantService":
        from src.app.modules.tenants.service import TenantService

        return TenantService
    if name == "crud_tenants":
        from src.app.modules.tenants.crud import crud_tenants

        return crud_tenants
    if name == "Tenant":
        from src.app.modules.tenants.models import Tenant

        return Tenant
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
