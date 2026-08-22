"""Unified Authorization Policy Framework.

Provides standard Protocol and implementations for resource-level authorization,
bridging RBAC permissions, ownership checks, and superuser bypasses.
"""

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from fastapi import Request
from sqlalchemy.exc import StatementError
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.logger import get_logger
from src.app.core.utils.user_utils import _get_user_attr

logger = get_logger(__name__)


@runtime_checkable
class ResourcePolicy(Protocol):
    """Protocol defining standard resource authorization evaluation."""

    async def evaluate(
        self,
        request: Request | None,
        user: dict[str, Any] | Any,
        resource: Any | None = None,
        db: AsyncSession | None = None,
    ) -> bool:
        """Evaluate if the user is authorized for the given resource or action.

        Args:
            request: Optional FastAPI HTTP request
            user: The authenticated user dictionary or AuthenticatedUser instance
            resource: Optional resource instance or dict being accessed/modified
            db: Optional async database session for DB-backed permission checks

        Returns:
            True if authorized, False otherwise.
        """
        ...


class RBACPolicy:
    """Policy requiring a specific RBAC permission slug or superuser status."""

    def __init__(self, permission: str, allow_superuser: bool = True) -> None:
        self.permission = permission
        self.allow_superuser = allow_superuser

    async def evaluate(
        self,
        request: Request | None,
        user: dict[str, Any] | Any,
        resource: Any | None = None,
        db: AsyncSession | None = None,
    ) -> bool:
        """Evaluate RBAC permission against user's bitmask."""
        # 1. Superuser bypass
        if self.allow_superuser and _get_user_attr(user, "is_superuser", False):
            return True

        if db is None:
            logger.warning("RBACPolicy evaluation failed: db AsyncSession is required")
            return False

        user_id = _get_user_attr(user, "id")
        tenant_id = _get_user_attr(user, "tenant_id")
        if not tenant_id:
            from src.app.core.tenancy.context import get_tenant_context

            tenant = get_tenant_context()
            tenant_id = tenant.id if tenant else None

        if not user_id or not tenant_id:
            logger.warning(
                "RBACPolicy evaluation failed: missing user_id or tenant_id",
                user_id=user_id,
                tenant_id=tenant_id,
            )
            return False

        from src.app.core.rbac.dependencies import has_permission_cached

        return await has_permission_cached(user_id, tenant_id, self.permission, db)


class OwnershipPolicy:
    """Policy requiring the user to own/author the resource, or have admin bypass permission."""

    def __init__(
        self,
        owner_field: str = "user_id",
        model: Any | None = None,
        allow_superuser: bool = True,
        admin_permission: str | None = None,
        id_param: str = "id",
    ) -> None:
        self.owner_field = owner_field
        self.model = model
        self.allow_superuser = allow_superuser
        self.admin_permission = admin_permission
        self.id_param = id_param

    async def evaluate(
        self,
        request: Request | None,
        user: dict[str, Any] | Any,
        resource: Any | None = None,
        db: AsyncSession | None = None,
    ) -> bool:
        """Evaluate if user is owner of resource or qualifies for admin bypass."""
        # 1. Superuser bypass
        if self.allow_superuser and _get_user_attr(user, "is_superuser", False):
            return True

        # 2. Admin permission bypass
        if self.admin_permission and db is not None:
            user_id = _get_user_attr(user, "id")
            tenant_id = _get_user_attr(user, "tenant_id")
            if not tenant_id:
                from src.app.core.tenancy.context import get_tenant_context

                tenant = get_tenant_context()
                tenant_id = tenant.id if tenant else None
            if user_id and tenant_id:
                from src.app.core.rbac.dependencies import has_permission_cached

                if await has_permission_cached(user_id, tenant_id, self.admin_permission, db):
                    return True

        user_id = _get_user_attr(user, "id")
        if not user_id:
            return False

        # 3. Check ownership on directly provided resource (also enforce tenant isolation)
        if resource is not None:
            owner_id = (
                _get_user_attr(resource, self.owner_field)
                if not isinstance(resource, dict)
                else resource.get(self.owner_field)
            )
            if owner_id != user_id:
                return False
            # Tenant isolation: if resource is tenant-scoped, its tenant_id must match caller's tenant
            res_tenant = _get_user_attr(resource, "tenant_id", None) if not isinstance(resource, dict) else resource.get("tenant_id")  # noqa: E501
            if res_tenant is not None:
                caller_tenant = _get_user_attr(user, "tenant_id")
                if not caller_tenant:
                    from src.app.core.tenancy.context import get_tenant_context
                    ctx = get_tenant_context()
                    caller_tenant = ctx.id if ctx else None
                if caller_tenant is not None and str(res_tenant) != str(caller_tenant):
                    logger.warning("OwnershipPolicy blocked cross-tenant access", resource_tenant=str(res_tenant), caller_tenant=str(caller_tenant))  # noqa: E501
                    return False
            return True

        # 4. If resource not provided, attempt DB lookup using request path params
        if request is not None and self.model is not None and db is not None:
            # Re-use already fetched resource if stashed in request state
            if hasattr(request.state, "authorized_resource") and request.state.authorized_resource is not None:
                resource = request.state.authorized_resource
                owner_id = (
                    _get_user_attr(resource, self.owner_field)
                    if not isinstance(resource, dict)
                    else resource.get(self.owner_field)
                )
                return bool(owner_id == user_id)

            item_id = request.path_params.get(self.id_param)
            if item_id is None:
                logger.warning("OwnershipPolicy evaluation failed: path param not found", param=self.id_param)
                return False

            try:
                converted_id = int(item_id) if isinstance(item_id, str) and item_id.isdigit() else item_id
                record = await db.get(self.model, converted_id)
                if record is None:
                    # Record not found; return False to prevent unauthorized scanning
                    return False

                # Tenant isolation before ownership: record.tenant_id must match caller's tenant
                rec_tenant = getattr(record, "tenant_id", None)
                if rec_tenant is not None:
                    caller_tenant2 = _get_user_attr(user, "tenant_id")
                    if not caller_tenant2:
                        from src.app.core.tenancy.context import get_tenant_context as _get_ctx
                        _ctx = _get_ctx()
                        caller_tenant2 = _ctx.id if _ctx else None
                    if caller_tenant2 is not None and str(rec_tenant) != str(caller_tenant2):
                        logger.warning("OwnershipPolicy blocked cross-tenant DB lookup", resource_tenant=str(rec_tenant), caller_tenant=str(caller_tenant2))  # noqa: E501
                        return False

                # Stash in request state for route handler reuse
                request.state.authorized_resource = record
                owner_id = getattr(record, self.owner_field, None)
                return bool(owner_id == user_id)
            except (ValueError, TypeError, StatementError) as e:
                logger.error("OwnershipPolicy DB lookup failed", error=str(e))
                return False

        return False


class AllOf:
    """Composite policy requiring ALL wrapped policies to evaluate True (logical AND)."""

    def __init__(self, *policies: ResourcePolicy) -> None:
        self.policies: Sequence[ResourcePolicy] = policies

    async def evaluate(
        self,
        request: Request | None,
        user: dict[str, Any] | Any,
        resource: Any | None = None,
        db: AsyncSession | None = None,
    ) -> bool:
        """Evaluate logical AND across policies."""
        for policy in self.policies:
            if not await policy.evaluate(request, user, resource, db):
                return False
        return True


class AnyOf:
    """Composite policy requiring AT LEAST ONE wrapped policy to evaluate True (logical OR)."""

    def __init__(self, *policies: ResourcePolicy) -> None:
        self.policies: Sequence[ResourcePolicy] = policies

    async def evaluate(
        self,
        request: Request | None,
        user: dict[str, Any] | Any,
        resource: Any | None = None,
        db: AsyncSession | None = None,
    ) -> bool:
        """Evaluate logical OR across policies."""
        for policy in self.policies:
            if await policy.evaluate(request, user, resource, db):
                return True
        return False
