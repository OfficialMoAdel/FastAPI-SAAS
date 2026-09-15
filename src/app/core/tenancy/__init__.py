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
    "clear_tenant_context",
    "get_current_tenant",
    "require_active_tenant",
    "set_current_tenant",
    "scoped_query",
    "tenant_scope",
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
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
