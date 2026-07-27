"""
RBAC Permission Dependency for FastAPI.

Provides permission checking via requires_permission() factory.
Uses bitmask integers for O(1) permission lookups with minimal Redis memory.

Bitmask Strategy:
- Each permission maps to a bit position (0-63)
- User's permissions stored as single integer in Redis
- Permission check: (user_mask & permission_bit) != 0
- Multiple role masks combined with OR
"""

from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from fastapi.params import Depends as DependsClass
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    pass
from src.app.core.exceptions.http_exceptions import ForbiddenException
from src.app.core.logger import get_logger
from src.app.core.rbac.bitmask import PermissionBitmask
from src.app.core.rbac.permissions import Permission as PermissionEnum
from src.app.core.rbac.utils import PERMISSIONS_CACHE_TTL
from src.app.core.utils.user_utils import _get_user_attr

logger = get_logger(__name__)

# OAuth2 scheme - same as used in api/dependencies.py
_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/account/login")


async def get_user_permissions(
    user_id: int,
    tenant_id: int | UUID,
    db: AsyncSession,
) -> set[str]:
    """
    Fetch all permission slugs for a user within a specific tenant.
    Queries UserRole -> Role -> RolePermission -> Permission.
    """
    from src.app.modules.rbac.models import Permission, Role, RolePermission, UserRole

    stmt = (
        select(Permission.slug)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .join(Role, Role.id == RolePermission.role_id)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(
            UserRole.user_id == user_id,
            UserRole.tenant_id == tenant_id,
        )
        .limit(1000)  # Safety bound
    )
    result = await db.execute(stmt)
    return set(result.scalars().all())




def _get_bitmask_cache_key(user_id: int, tenant_id: int | UUID) -> str:
    """Get Redis key for bitmask-based permission cache."""
    # Append version hash to invalidate cache when permissions change (e.g. new deployment)
    version = PermissionBitmask.get_permission_version_hash()
    return f"perms:bitmask:{tenant_id}:{user_id}:{version}"


async def get_cached_permissions_bitmask(
    user_id: int,
    tenant_id: int | UUID,
    db: AsyncSession,
) -> int:
    """
    Get user permissions as bitmask with Redis caching.

    Benefits over Redis Sets:
    - Single integer vs set of 20+ strings (lower memory)
    - Single GET vs SMEMBERS (faster retrieval)
    - Bitwise operations for permission checks (no network round-trip per check)

    Args:
        user_id: The user's ID
        tenant_id: The tenant's ID
        db: Database session for fallback query

    Returns:
        Integer bitmask with bits set for each permission
    """
    from src.app.core.utils.cache import client as redis_client

    cache_key = _get_bitmask_cache_key(user_id, tenant_id)

    # Try to get bitmask from cache
    if redis_client is not None:
        try:
            cached = await redis_client.get(cache_key)
            if cached is not None:
                # Decode bytes to int
                return int(cached.decode() if isinstance(cached, bytes) else cached)
        except Exception as e:
            logger.warning(
                "Failed to read bitmask from cache",
                user_id=user_id,
                tenant_id=tenant_id,
                error=str(e),
            )

    # Query from database
    permissions = await get_user_permissions(user_id, tenant_id, db)

    # Encode to bitmask
    mask = PermissionBitmask.encode(permissions)

    # Cache the bitmask
    if redis_client is not None:
        try:
            await redis_client.setex(cache_key, PERMISSIONS_CACHE_TTL, str(mask))
        except Exception as e:
            logger.warning(
                "Failed to cache bitmask",
                user_id=user_id,
                tenant_id=tenant_id,
                error=str(e),
            )

    return mask


async def has_permission_cached(
    user_id: int,
    tenant_id: int | UUID,
    permission_slug: str,
    db: AsyncSession,
) -> bool:
    """
    Check if a user has a specific permission using O(1) bitmask check.

    Uses cached bitmask for pure in-memory bitwise check after initial Redis GET.
    This is more efficient than Redis SISMEMBER as it:
    - Retrieves single integer vs checking membership
    - Enables multiple permission checks without additional network calls
    - Uses less Redis memory (single integer vs set of strings)

    Args:
        user_id: The user's ID
        tenant_id: The tenant's ID
        permission_slug: The permission to check (e.g., "users:read")
        db: Database session for fallback query

    Returns:
        True if user has the permission, False otherwise
    """
    # Get user's permission bitmask (from cache or DB)
    mask = await get_cached_permissions_bitmask(user_id, tenant_id, db)

    # O(1) bitwise check: (mask & permission_bit) != 0
    return PermissionBitmask.has_permission(mask, permission_slug)


def requires_permission(permission: PermissionEnum | str) -> Callable[..., Any]:
    """
    FastAPI dependency factory that checks if the current user has a specific permission.

    Usage:
        from src.app.core.rbac import Permission, requires_permission

        @router.get("/roles", dependencies=[Depends(requires_permission(Permission.ROLES_READ))])
        async def list_roles():
            ...

    Logic:
        1. Superusers bypass all permission checks
        2. Check Redis cache for user's permissions
        3. If not cached, query DB and cache the result
        4. Raise 403 Forbidden if permission not found
    """
    permission_slug = permission.value if isinstance(permission, PermissionEnum) else permission

    from src.app.api.dependencies import get_auth_context
    from src.app.core.db.database import async_get_db

    async def _check_permission(
        request: Request = None,  # type: ignore[assignment]
        token: str = Depends(_oauth2_scheme),
        db: AsyncSession = Depends(async_get_db),
        auth_context: Any = Depends(get_auth_context),
    ) -> None:
        is_dependency_override = False
        if (
            auth_context is None
            or isinstance(auth_context, DependsClass)
            or hasattr(auth_context, "dependency")
            or type(auth_context).__name__ == "Depends"
        ):
            # Fall back to resolving the user manually using _get_current_user_from_token(token, db)
            user_data = await _get_current_user_from_token(token, db)
            from src.app.api.dependencies import AuthenticatedUser

            if isinstance(user_data, dict):
                from src.app.core.config import settings

                # Ensure we have email/username fallback if missing
                if "email" not in user_data:
                    if settings.ENVIRONMENT != "production":
                        user_data["email"] = "mocked@example.com"
                    else:
                        user_data["email"] = "unknown@example.com"
                if "username" not in user_data:
                    if settings.ENVIRONMENT != "production":
                        user_data["username"] = "mockeduser"
                    else:
                        user_data["username"] = "unknown"
                current_user = AuthenticatedUser(**user_data)
            else:
                current_user = user_data

            # Retrieve tenant_context from get_tenant_context()
            from src.app.core.tenancy.context import get_tenant_context

            tenant_context = get_tenant_context()
            is_dependency_override = True
        else:
            tenant_context = auth_context.tenant
            current_user = auth_context.user
            is_dependency_override = auth_context.is_dependency_override

        if tenant_context is None and request is not None:
            tenant_id_in_path = request.path_params.get("tenant_id")
            if tenant_id_in_path:
                from src.app.core.tenancy.context import TenantContext
                from src.app.modules.tenants.models import Tenant

                try:
                    tenant_uuid = UUID(str(tenant_id_in_path))
                except ValueError:
                    tenant_uuid = tenant_id_in_path

                stmt = select(Tenant).where(Tenant.id == tenant_uuid)
                res = await db.execute(stmt)
                tenant_model = res.scalar_one_or_none()
                if tenant_model:
                    tenant_context = TenantContext.from_model(tenant_model)

        if tenant_context is None:
            # SECURITY FIX: Never fall back to a default tenant ID
            # Permission checks require valid tenant context
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Tenant context required for permission check",
            )
        tenant_id = tenant_context.id

        is_superuser = _get_user_attr(current_user, "is_superuser", False)
        user_id_val = _get_user_attr(current_user, "id")

        if user_id_val is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User ID is required.",
            )
        user_id = int(user_id_val)

        # Enforce tenant isolation before any permission bypass. Superusers may bypass
        # permission checks within their tenant, but they cannot act as another tenant.
        # Test dependency overrides are excluded because they are not production auth.
        current_user_tenant_id = _get_user_attr(current_user, "tenant_id")

        if (
            not is_dependency_override
            and current_user_tenant_id is not None
            and str(current_user_tenant_id) != str(tenant_id)
        ):
            raise ForbiddenException("User does not belong to the current tenant.")

        if is_superuser:
            logger.info(
                "Superuser bypass used",
                user_id=user_id,
                tenant_id=str(tenant_id),
                permission=permission_slug,
            )
            return

        # Check permission using O(1) bitmask
        has_perm = await has_permission_cached(user_id, tenant_id, permission_slug, db)

        # Raise if permission denied
        if not has_perm:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission denied: {permission_slug}",
            )

    return _check_permission


async def invalidate_user_permissions_cache(user_id: int, tenant_id: int | UUID) -> None:
    """
    Invalidate the cached permissions for a user in a specific tenant.
    Clears both Set-based cache and bitmask cache for backward compatibility.
    Call this when user roles are modified.
    """
    from src.app.core.utils.cache import client as redis_client

    if redis_client is None:
        return

    bitmask_cache_key = _get_bitmask_cache_key(user_id, tenant_id)

    try:
        # Delete cache
        await redis_client.delete(bitmask_cache_key)
    except Exception as e:
        # Log cache invalidation failure
        logger.warning(
            "Failed to invalidate permissions cache",
            user_id=user_id,
            tenant_id=tenant_id,
            error=str(e),
        )


async def invalidate_users_permissions_cache_batch(user_ids: list[int], tenant_id: int | UUID) -> None:
    """
    Invalidate the cached permissions for multiple users in a specific tenant in a single pipeline.
    Call this when a role assigned to many users is modified.
    """
    if not user_ids:
        return

    from src.app.core.utils.cache import client as redis_client

    if redis_client is None:
        return

    try:
        async with redis_client.pipeline() as pipe:
            for user_id in user_ids:
                bitmask_cache_key = _get_bitmask_cache_key(user_id, tenant_id)
                pipe.delete(bitmask_cache_key)

            # Execute all deletions in one round-trip
            await pipe.execute()  # type: ignore[union-attr]
    except Exception as e:
        logger.warning(
            "Failed to batch invalidate permissions cache",
            user_count=len(user_ids),
            tenant_id=tenant_id,
            error=str(e),
        )


# Private helpers


async def _get_current_user_from_token(token: str, db: AsyncSession) -> dict[str, Any]:
    """Validate token and return current user dict using get_auth_context."""
    from src.app.api.dependencies import get_auth_context

    try:
        auth_context = await get_auth_context(request=None, db=db, token=token)  # type: ignore[arg-type]
        return auth_context.user.model_dump()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in _get_current_user_from_token: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _get_current_tenant_id() -> UUID:
    """Get current tenant ID from middleware context."""
    from src.app.core.tenancy.context import get_tenant_context

    tenant_context = get_tenant_context()
    if tenant_context is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tenant context not set. Ensure X-Tenant-ID header is provided.",
        )
    return tenant_context.id
