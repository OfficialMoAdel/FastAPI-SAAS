"""RequestContext: the single injectable request principal.

Replaces the repeated two/three-dependency ceremony at endpoint signature
level::

    # Before (repeated at ~150 parameter positions):
    tenant: Tenant = Depends(tenant_dependency),
    _user: Any = Depends(get_current_user),
    service: FileService = Depends(get_file_service),

    # After (one argument):
    ctx: RequestContextDep,
    service: FileService = Depends(get_file_service),

Design notes
------------
* ``RequestContext`` composes the *existing* cached dependencies
  (``get_current_user``, ``tenant_dependency``, ``async_get_db``).
  FastAPI caches dependency results per-request, so this adds **zero**
  extra authentication lookups or DB sessions per request.
* The concept "the current request's principal inside its tenant" now has
  one deep type: authorization checks and audit metadata have a single
  home instead of being scattered across routers.
* Tests construct a ``RequestContext`` directly (or override
  ``get_request_context``); no module-global reload hacks needed.

Usage example::

    from src.app.api.deps import RequestContextDep

    @router.post("/upload")
    async def upload_file(
        ctx: RequestContextDep,
        service: FileService = Depends(get_file_service),
    ) -> Any:
        result = await service.upload_file(
            tenant_id=ctx.tenant_id,
            user_id=ctx.user_id,
            ...
        )
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, cast
from uuid import UUID

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.deps.auth import get_current_user
from src.app.api.deps.tenant import tenant_dependency
from src.app.core.db.database import async_get_db
from src.app.core.principal import AuthenticatedUser
from src.app.core.security.ownership import owns_resource, require_resource_ownership
from src.app.core.tenancy.context import (  # noqa: F401 — re-exported canonical domain error
    NO_TENANT,
    TenantContext,
    TenantContextError,
)


@dataclass(frozen=True, slots=True)
class RequestContext:
    """The current request's authenticated principal within its tenant.

    Attributes:
        user: The authenticated user (validated by ``get_auth_context``:
            active, not deleted, token version matched, session valid).
        tenant: The resolved tenant context (set by ``TenantMiddleware``
            and validated by ``get_auth_context`` for tenant membership).
        db: The request-scoped asynchronous database session.

    WARNING (P2 lifecycle contract): ``db`` is bound to the REQUEST
    lifecycle — it is closed when the request ends. Never hand a
    ``RequestContext`` (or ``ctx.db``) to a background task / ARQ job /
    ``asyncio.create_task`` that may outlive the request; carry ids and
    the principal instead and open a fresh session via
    ``core.db.database.local_session`` (worker paths use the
    ``job_*_context`` seams). A full "ids/principal only" refactor of
    this dataclass is tracked as follow-up work; until then this
    contract is enforced by review, not types.
    """

    user: AuthenticatedUser
    tenant: TenantContext
    db: AsyncSession

    @property
    def user_id(self) -> int:
        """The authenticated user's integer id."""
        return self.user.id

    @property
    def tenant_id(self) -> UUID:
        """The current tenant id (alias-safe across model/context types)."""
        value = getattr(self.tenant, "tenant_id", None)
        if value is None:
            value = getattr(self.tenant, "id", None)
        return cast("UUID", value)

    @property
    def is_superuser(self) -> bool:
        """Whether the principal bypasses normal tenant scoping."""
        return self.user.is_superuser

    @property
    def has_tenant(self) -> bool:
        """Whether a real (non-sentinel) tenant context is bound."""
        return bool(self.tenant) and self.tenant is not NO_TENANT

    def require_tenant(self) -> TenantContext:
        """Return the tenant context or raise if this is a tenant-less path.

        P3-#14 (one no-tenant contract): raises ``TenantContextError``
        (403 via the central table) — the same exception
        :func:`require_active_tenant` and ``tenant_dependency`` raise —
        so every no-tenant seam speaks one status from one class.

        Raises:
            TenantContextError: If no real tenant context is available.
        """
        if not self.has_tenant:
            raise TenantContextError("Tenant context is required for this operation.")
        return self.tenant

    def owns(self, owner_user_id: object) -> bool:
        """Whether the principal owns a resource belonging to ``owner_user_id``."""
        return owns_resource(self.user_id, owner_user_id)

    def require_ownership(self, owner_user_id: object) -> None:
        """Raise unless the principal owns the resource.

        Thin wrapper over :func:`require_resource_ownership` for the
        request layer; services call the pure function directly so they
        stay ``RequestContext``-free.

        Raises:
            ForbiddenException: If the resource belongs to another user.
        """
        require_resource_ownership(owner_user_id, self.user_id, self.is_superuser)


async def get_request_context(
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    tenant: Annotated[TenantContext, Depends(tenant_dependency)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> RequestContext:
    """Build the request context from existing cached dependencies.

    Every underlying dependency is already resolved (and cached by
    FastAPI's dependency system) when any of them is used elsewhere in
    the same request, so this wrapper costs nothing additional.
    """
    return RequestContext(user=user, tenant=tenant, db=db)


# Annotated alias for endpoint signatures.
RequestContextDep = Annotated[RequestContext, Depends(get_request_context)]

__all__ = [
    "RequestContext",
    "RequestContextDep",
    "get_request_context",
    "owns_resource",
    "require_resource_ownership",
]
