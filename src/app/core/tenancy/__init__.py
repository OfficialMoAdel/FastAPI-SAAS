"""Tenancy core module - tenant context management + query seam.

Provides request-scoped tenant context via ContextVar for async-safe
tenant isolation across concurrent requests, and the single tenant-filter
criterion (:func:`tenant_scope` / :func:`scoped_query`) that every
tenant-shaped query composes onto.

Direct imports:
    from src.app.core.tenancy.context import (
        get_current_tenant,
        set_current_tenant,
        clear_tenant_context,
        require_active_tenant,
    )
    from src.app.core.tenancy.query import tenant_scope, scoped_query
"""

__all__ = [
    "TENANT_WIRE_KEYS",
    "TenantWireError",
    "bind_tenant",
    "bound_tenant",
    "clear_tenant_context",
    "fabricate_job_tenant",
    "get_current_tenant",
    "is_valid_slug",
    "negative_cache_key",
    "normalize_slug",
    "preserved_tenant",
    "require_active_tenant",
    "resolve_tenant_by_slug",
    "set_current_tenant",
    "scoped_query",
    "slug_cache_key",
    "tenant_cache_data_from_model",
    "tenant_context_from_cache_data",
    "tenant_from_wire",
    "tenant_scope",
    "tenant_to_wire",
    "unbind_tenant",
]


from typing import Any


def __getattr__(name: str) -> Any:
    """Lazy import to avoid circular imports."""
    if name == "get_current_tenant":
        from src.app.core.tenancy.context import get_current_tenant

        return get_current_tenant
    if name == "set_current_tenant":
        from src.app.core.tenancy.context import set_current_tenant

        return set_current_tenant
    if name == "clear_tenant_context":
        from src.app.core.tenancy.context import clear_tenant_context

        return clear_tenant_context
    if name == "require_active_tenant":
        from src.app.core.tenancy.context import require_active_tenant

        return require_active_tenant
    if name == "scoped_query":
        from src.app.core.tenancy.query import scoped_query

        return scoped_query
    if name == "tenant_scope":
        from src.app.core.tenancy.query import tenant_scope

        return tenant_scope
    if name in {
        "is_valid_slug",
        "negative_cache_key",
        "normalize_slug",
        "resolve_tenant_by_slug",
        "slug_cache_key",
        "tenant_cache_data_from_model",
        "tenant_context_from_cache_data",
    }:
        from src.app.core.tenancy import resolver as _resolver

        return getattr(_resolver, name)
    if name in {
        "TENANT_WIRE_KEYS",
        "TenantWireError",
        "bind_tenant",
        "bound_tenant",
        "fabricate_job_tenant",
        "preserved_tenant",
        "tenant_from_wire",
        "tenant_to_wire",
        "unbind_tenant",
    }:
        from src.app.core.tenancy import lifecycle as _lifecycle

        return getattr(_lifecycle, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
