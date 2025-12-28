"""Tenancy core module - tenant context management.

Provides request-scoped tenant context via ContextVar for async-safe
tenant isolation across concurrent requests.

Direct imports:
    from src.app.core.tenancy.context import get_tenant_context, set_tenant_context
"""

__all__ = [
    "get_tenant_context",
    "set_tenant_context",
    "clear_tenant_context",
]


def __getattr__(name: str):
    """Lazy import to avoid circular imports."""
    if name == "get_tenant_context":
        from src.app.core.tenancy.context import get_tenant_context

        return get_tenant_context
    if name == "set_tenant_context":
        from src.app.core.tenancy.context import set_tenant_context

        return set_tenant_context
    if name == "clear_tenant_context":
        from src.app.core.tenancy.context import clear_tenant_context

        return clear_tenant_context
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
