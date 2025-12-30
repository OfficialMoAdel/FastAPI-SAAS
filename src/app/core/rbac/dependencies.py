"""
RBAC Permission Dependency for FastAPI.

Provides permission checking via requires_permission() factory.
Uses Redis caching for performance.
"""

import json
from collections.abc import Callable
from typing import Any

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .permissions import Permission as PermissionEnum
from .utils import PERMISSIONS_CACHE_TTL, get_user_permissions_cache_key

# OAuth2 scheme - same as used in api/dependencies.py
_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/login")


async def get_user_permissions(
    user_id: int,
    tenant_id: int,
    db: AsyncSession,
) -> set[str]:
    """
    Fetch all permission slugs for a user within a specific tenant.
    Queries UserRole -> Role -> RolePermission -> Permission.
    """
    from ...modules.rbac.models import Permission, Role, RolePermission, UserRole

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
    tenant_id: int,
    db: AsyncSession,
) -> set[str]:
    """
    Get user permissions with Redis caching.
    Returns cached permissions if available, otherwise queries DB and caches result.
    """
    from ...core.utils.cache import client as redis_client

    cache_key = get_user_permissions_cache_key(user_id, tenant_id)

    # Try to get from cache
    if redis_client is not None:
        try:
            cached = await redis_client.get(cache_key)
            if cached:
                return set(json.loads(cached))
        except Exception as e:
            # Log cache read failure but continue to DB query
            import logging

            logging.getLogger(__name__).warning(
                f"Failed to read permissions from cache for user {user_id}, tenant {tenant_id}: {e}"
            )

    # Query from database
    permissions = await get_user_permissions(user_id, tenant_id, db)

    # Cache the result
    if redis_client is not None:
        try:
            await redis_client.set(cache_key, json.dumps(list(permissions)))
            await redis_client.expire(cache_key, PERMISSIONS_CACHE_TTL)
        except Exception as e:
            # Log cache write failure but continue successfully (permissions already retrieved)
            import logging

            logging.getLogger(__name__).warning(
                f"Failed to cache permissions for user {user_id}, tenant {tenant_id}: {e}"
            )

    return permissions


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

    async def _check_permission(
        token: str = Depends(_oauth2_scheme),
        db: AsyncSession = Depends(_get_db),
    ) -> None:
        # Get current user (inline to avoid circular import)
        current_user = await _get_current_user_from_token(token, db)

        # 1. Superuser bypass
        if current_user.get("is_superuser", False):
            return

        # 2. Get tenant context
        tenant_id = _get_current_tenant_id()

        # 3. Get permissions (from cache or DB)
        user_permissions = await get_cached_permissions(current_user["id"], tenant_id, db)

        # 4. Check permission
        if permission_slug not in user_permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission denied: {permission_slug}",
            )

    return _check_permission


async def invalidate_user_permissions_cache(user_id: int, tenant_id: int) -> None:
    """
    Invalidate the cached permissions for a user in a specific tenant.
    Call this when user roles are modified.
    """
    from ...core.utils.cache import client as redis_client

    if redis_client is None:
        return

    cache_key = get_user_permissions_cache_key(user_id, tenant_id)
    try:
        await redis_client.delete(cache_key)
    except Exception as e:
        # Log cache invalidation failure
        import logging

        logging.getLogger(__name__).warning(
            f"Failed to invalidate permissions cache for user {user_id}, tenant {tenant_id}: {e}"
        )


# Private helpers


async def _get_db() -> AsyncSession:
    """Get async database session."""
    from ...core.db.database import async_get_db

    async for db in async_get_db():
        yield db


async def _get_current_user_from_token(token: str, db: AsyncSession) -> dict[str, Any]:
    """
    Validate token and return current user dict.
    Mirrors logic from api/dependencies.py to avoid circular import.
    """
    from ...core.security import TokenType, verify_token
    from ...modules.users.crud import crud_users

    token_data = await verify_token(token, TokenType.ACCESS, db)
    if token_data is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Fetch user by email or username
    if "@" in token_data.username_or_email:
        user = await crud_users.get(db=db, email=token_data.username_or_email, is_deleted=False)
    else:
        user = await crud_users.get(db=db, username=token_data.username_or_email, is_deleted=False)

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


def _get_current_tenant_id() -> int:
    """Get current tenant ID from middleware context."""
    from ..tenancy.context import get_tenant_context

    tenant_context = get_tenant_context()
    if tenant_context is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tenant context not set. Ensure X-Tenant-ID header is provided.",
        )
    return tenant_context.tenant_id
