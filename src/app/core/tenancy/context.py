"""Tenant context management using ContextVar for async-safe isolation.

This module provides request-scoped tenant context that is:
- Async-safe: Each async task gets its own context copy
- Isolated: No context leakage between concurrent requests
- Explicit: Context must be explicitly set and cleared
"""

from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.app.modules.tenants.models import Tenant

_current_tenant: ContextVar["Tenant | None"] = ContextVar("current_tenant", default=None)


def get_tenant_context() -> "Tenant | None":
    """Get the current tenant from the context.

    Returns
    -------
        The current tenant if set, None otherwise.
    """
    return _current_tenant.get()


def set_tenant_context(tenant: "Tenant | None") -> None:
    """Set the current tenant in the context.

    Args:
        tenant: The tenant to set as current, or None to clear.
    """
    _current_tenant.set(tenant)


def clear_tenant_context() -> None:
    """Clear the current tenant from the context.

    Should be called in a finally block after request processing
    to prevent context leakage.
    """
    _current_tenant.set(None)
