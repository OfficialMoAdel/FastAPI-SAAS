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
from typing import Any
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.logger import get_logger
from src.app.core.rbac.bitmask import PermissionBitmask
from src.app.core.rbac.permissions import Permission as PermissionEnum
from src.app.core.rbac.utils import PERMISSIONS_CACHE_TTL, get_user_permissions_cache_key

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
    )
    result = await db.execute(stmt)
    return set(result.scalars().all())


async def get_cached_permissions(
    user_id: int,
    tenant_id: int | UUID,
    db: AsyncSession,
) -> set[str]:
    """
    Get user permissions with Redis caching using native Sets.

    Uses Redis SMEMBERS for O(1) retrieval instead of JSON deserialization.
    Returns cached permissions if available, otherwise queries DB and caches result.
    """
    from src.app.core.utils.cache import client as redis_client

    cache_key = get_user_permissions_cache_key(user_id, tenant_id)

    # Try to get from cache using Redis Sets
    if redis_client is not None:
        try:
            # Check if set exists and get all members in one call
            if await redis_client.exists(cache_key):
                members = await redis_client.smembers(cache_key)  # type: ignore[misc]
                # Redis returns bytes, decode to strings
                return {m.decode() if isinstance(m, bytes) else m for m in members}  # type: ignore[union-attr]
        except Exception as e:
            # Log cache read failure but continue to DB query
            logger.warning(
                "Failed to read permissions from cache",
                user_id=user_id,
                tenant_id=tenant_id,
                error=str(e),
            )

    # Query from database
    permissions = await get_user_permissions(user_id, tenant_id, db)

    # Cache the result using Redis Sets
    if redis_client is not None and permissions:
        try:
            # Use pipeline for atomic set + expire
            async with redis_client.pipeline() as pipe:
                pipe.delete(cache_key)  # Clear any existing data
                pipe.sadd(cache_key, *permissions)
                pipe.expire(cache_key, PERMISSIONS_CACHE_TTL)
                await pipe.execute()  # type: ignore[union-attr]
        except Exception as e:
            # Log cache write failure but continue successfully (permissions already retrieved)
            logger.warning(
                "Failed to cache permissions",
                user_id=user_id,
                tenant_id=tenant_id,
                error=str(e),
            )

    return permissions


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

    from src.app.core.db.database import async_get_db

    async def _check_permission(
        token: str = Depends(_oauth2_scheme),
        db: AsyncSession = Depends(async_get_db),
    ) -> None:
        # 1. Get current user FIRST to check for superuser bypass
        current_user = await _get_current_user_from_token(token, db)

        # 2. Get tenant context (required for everyone, even superusers, to ensure operations are scoped)
        from src.app.core.tenancy.context import get_tenant_context

        tenant_context = get_tenant_context()
        if tenant_context is None:
            # SECURITY FIX: Never fall back to a default tenant ID
            # Permission checks require valid tenant context
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Tenant context required for permission check",
            )
        tenant_id = tenant_context.id

        # 3. Superuser bypass - skip permission checks, but AFTER verifying tenant context exists
        # SECURITY HARDENING: Log superuser bypass and verify system-level authorization.
        if current_user.get("is_superuser", False):
            # SECURITY FIX: Prevent cache persistence window by verifying live DB state for superusers
            from sqlalchemy import select

            from src.app.modules.users.models import User

            stmt = select(User.is_superuser).where(User.id == current_user["id"])
            is_really_su = await db.scalar(stmt)

            if is_really_su:
                logger.info(
                    "Superuser bypass used",
                    user_id=current_user["id"],
                    tenant_id=str(tenant_id),
                    permission=permission_slug
                )
                return

        # 4. Check permission using O(1) bitmask
        has_perm = await has_permission_cached(current_user["id"], tenant_id, permission_slug, db)

        # 5. Raise if permission denied
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

    set_cache_key = get_user_permissions_cache_key(user_id, tenant_id)
    bitmask_cache_key = _get_bitmask_cache_key(user_id, tenant_id)

    try:
        # Delete both cache types atomically
        async with redis_client.pipeline() as pipe:
            pipe.delete(set_cache_key)
            pipe.delete(bitmask_cache_key)
            await pipe.execute()  # type: ignore[union-attr]
    except Exception as e:
        # Log cache invalidation failure
        logger.warning(
            "Failed to invalidate permissions cache",
            user_id=user_id,
            tenant_id=tenant_id,
            error=str(e),
        )


# Private helpers





async def _get_current_user_from_token(token: str, db: AsyncSession) -> dict[str, Any]:
    """
    Validate token and return current user dict.
    Mirrors logic from api/dependencies.py to avoid circular import.

    GI-2: Uses UserService.get_user_auth_info() which has Redis caching,
    instead of direct crud_users.get() which bypasses cache on every request.
    """
    from src.app.core.security import TokenType, verify_token
    from src.app.modules.users.service import UserService

    token_data = await verify_token(token, TokenType.ACCESS, db)
    if token_data is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # GI-2: Use UserService with Redis caching instead of direct DB query
    user_service = UserService(db=db)

    # Fetch user by email or username (cached)
    if "@" in token_data.username_or_email:
        user = await user_service.get_user_auth_info(email=token_data.username_or_email)
    else:
        user = await user_service.get_user_auth_info(username=token_data.username_or_email)

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.get("is_active", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is no longer active",
        )

    return user


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
