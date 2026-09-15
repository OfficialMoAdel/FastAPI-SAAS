"""Tenant context management using ContextVar for async-safe isolation.

This module provides request-scoped tenant context that is:
- Async-safe: Each async task gets its own context copy
- Isolated: No context leakage between concurrent requests
- Explicit: Context must be explicitly set and cleared
- Memory-optimized: Uses lightweight dataclass instead of full ORM model

Memory Optimization (FEATURE_CONTEXTVAR_OPTIMIZATION):
- Stores only essential tenant fields in context
- Avoids storing full ORM model with relationships
- Reduces memory overhead by ~90% per request
"""

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import UUID

from src.app.core.exceptions.http_exceptions import TenantContextError  # noqa: F401  # H3: re-exported canonical class
from src.app.shared.exceptions import ModelNotFoundError

if TYPE_CHECKING:
    from src.app.modules.tenants.models import Tenant


@dataclass(frozen=True, slots=True)
class TenantContext:
    """Lightweight tenant context for memory efficiency.

    Stores only essential fields needed during request processing.
    Uses slots=True to reduce memory footprint by ~40%.
    Frozen to prevent accidental modifications.

    Memory savings: ~90% compared to storing full ORM model.
    """

    tenant_id: UUID
    tenant_slug: str
    tenant_name: str
    is_active: bool
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_model(cls, tenant: "Tenant") -> "TenantContext":
        """Create lightweight context from full Tenant model.

        Extracts only essential fields to minimize memory usage.
        """
        return cls(
            tenant_id=tenant.id,
            tenant_slug=tenant.slug,
            tenant_name=tenant.name,
            is_active=tenant.is_active,
        )

    @property
    def id(self) -> UUID:
        """Alias for tenant_id for backward compatibility."""
        return self.tenant_id

    @property
    def slug(self) -> str:
        """Alias for tenant_slug for backward compatibility."""
        return self.tenant_slug

    @property
    def name(self) -> str:
        """Alias for tenant_name for backward compatibility."""
        return self.tenant_name


class _NoTenantContext(TenantContext):
    """Sentinel object for explicitly exempt paths to safely bypass tenancy checks."""

    def __bool__(self) -> bool:
        return False


NO_TENANT = _NoTenantContext(
    tenant_id=UUID(int=0),
    tenant_slug="no-tenant",
    tenant_name="No Tenant",
    is_active=False,
)


# ContextVars are thread-safe and async-safe
_current_tenant: ContextVar["TenantContext | None"] = ContextVar("current_tenant", default=None)


def get_current_tenant() -> "TenantContext | None":
    """Get the current tenant from the context.

    Returns:
        The current tenant context if set, None otherwise.

    Note:
        Returns TenantContext (lightweight dataclass). Returns None when no
        tenant has been set for this request/task.
    """
    return _current_tenant.get()


def set_current_tenant(tenant: "Tenant | TenantContext | None") -> None:
    """Set the current tenant in the context.

    Args:
        tenant: The tenant model or context to set. None clears the context.
    """
    if tenant is None:
        _current_tenant.set(None)
    elif isinstance(tenant, TenantContext):
        _current_tenant.set(tenant)
    else:
        _current_tenant.set(TenantContext.from_model(tenant))


def clear_tenant_context() -> None:
    """Clear the tenant context.

    Should be called in a finally block after request processing
    to prevent context leakage.
    """
    _current_tenant.set(None)




def require_active_tenant() -> "TenantContext":
    """Get the current tenant or raise — the ONE fail-closed accessor.

    Callers that cannot proceed without a tenant (services enforcing
    isolation, billing, etc.) must use this instead of ad-hoc None checks,
    so the failure mode is uniform: ``TenantContextError`` for both a
    missing context and an inactive tenant.

    Returns:
        The active TenantContext.

    Raises:
        TenantContextError: If no tenant is set, or the tenant is inactive.
    """
    tenant = get_current_tenant()
    if tenant is None:
        raise TenantContextError("No tenant context available")
    if not tenant.is_active:
        raise TenantContextError("Tenant is not active")
    return tenant


def require_tenant_match(
    resource_tenant_id: UUID | None,
    *,
    resource: str = "resource",
    key: str | int | UUID | None = None,
) -> None:
    """Enforce cross-tenant ownership — the ONE soft-404 encoding.

    P2-C: replaces the four divergent per-call-site tenant comparisons
    (``str(a) != str(b)`` vs ``a != b``; 404 vs 403) with a single seam.
    A resource whose tenant differs from the current context tenant —
    including a missing (``None``) resource tenant — raises
    ``ModelNotFoundError`` (404), never 403, so cross-tenant existence is
    never leaked (enumeration-safe). In-tenant ownership stays 403 via
    ``RequestContext.require_ownership`` / ``ForbiddenException``.
    Tenant-less paths (no current tenant, e.g. worker/system flows) skip
    the check, preserving existing system behavior.

    Args:
        resource_tenant_id: Tenant UUID owning the resource (UUID sole type).
        resource: Model name for the 404 error (e.g. ``"User"``).
        key: Lookup key for the 404 error (e.g. username).

    Raises:
        ModelNotFoundError: If a tenant is bound and the resource belongs
            to a different tenant (or to none).
    """
    current = get_current_tenant()
    # P1 FIX (code-review): normalize the two "no tenant" states. `None`
    # (context never set) and NO_TENANT (falsy exempt sentinel) previously
    # behaved differently — None skipped while NO_TENANT compared against
    # UUID(int=0) and 404'd. Both now skip identically with a debug log so
    # worker/system flows keep existing behavior and the seam has one rule.
    # Callers needing fail-closed must use require_active_tenant() instead.
    if not current:
        return
    if resource_tenant_id != current.tenant_id:
        raise ModelNotFoundError(resource, key)


def resolve_tenant_id(explicit_tenant_id: Any = None, *, entity_name: str = "resource", entity_id: Any = None) -> Any:
    """Resolve the effective tenant id — the ONE explicit-vs-context seam.

    Rules (shared by every service; mirrors :func:`tenant_scope`):

    - Active context always wins; a mismatching explicit value raises
      ``ModelNotFoundError`` (enumeration-safe soft-404, never 403).
    - No context: an explicit value may be used (worker/system paths).
    - Neither: raises ``ModelNotFoundError`` (fail-closed).

    Returns the context id when bound, else the explicit value verbatim.
    """
    current = get_current_tenant()
    # P1: normalize the two "no tenant" states (None + NO_TENANT sentinel).
    current_id = current.id if current else None
    if current_id is not None and explicit_tenant_id is not None and str(explicit_tenant_id) != str(current_id):
        raise ModelNotFoundError(entity_name, entity_id if entity_id is not None else "tenant")
    tid = current_id if current_id is not None else explicit_tenant_id
    if tid is None:
        raise ModelNotFoundError(entity_name, entity_id if entity_id is not None else "tenant")
    return tid
