"""User API router - HTTP handlers delegating to service layer.

RequestContext pattern (ADR-0001): every endpoint takes ``ctx:
RequestContextDep`` — one argument bundling the authenticated user, tenant
context, and request-scoped DB session. Ownership/IDOR checks go through
``ctx.require_ownership()`` (the single ownership seam) instead of inline
username/tenant comparisons.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.deps import RequestContextDep, get_current_superuser
from src.app.core.db.database import async_get_db
from src.app.core.pagination import PaginationParams, get_pagination_params
from src.app.core.rbac.dependencies import requires_permission
from src.app.core.rbac.permissions import Permission
from src.app.core.security import oauth2_scheme
from src.app.modules.users.schemas import (
    UserCreate,
    UserDeleteResponse,
    UserList,
    UserRead,
    UserReadPublic,
    UserTierResponse,
    UserTierUpdate,
    UserUpdate,
    UserUpdateResponse,
)
from src.app.modules.users.service import UserService, get_user_service

router = APIRouter(prefix="/users", tags=["users"])


async def _get_user_service(db: AsyncSession = Depends(async_get_db)) -> Any:
    """Dependency for UserService injection."""
    return get_user_service(db)


@router.post(
    "",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requires_permission(Permission.USERS_MANAGE))],
)
async def write_user(request: Request, user: UserCreate, service: UserService = Depends(_get_user_service)) -> Any:
    """Create a new user."""
    return await service.create(user)


@router.get(
    "",
    response_model=UserList,
    dependencies=[Depends(requires_permission(Permission.USERS_READ))],
)
async def read_users(
    request: Request,
    pagination: PaginationParams = Depends(get_pagination_params),
    service: UserService = Depends(_get_user_service),
) -> Any:
    """Get paginated list of users for the current tenant (superuser only)."""
    return await service.list_users(
        page=pagination.page,
        per_page=pagination.per_page,
    )


@router.get("/me", response_model=UserReadPublic)
async def read_users_me(
    request: Request,
    ctx: RequestContextDep,
    service: UserService = Depends(_get_user_service),
) -> Any:
    """Get current authenticated user profile.

    `ctx.user` returns auth-oriented payload; fetch full user profile
    to satisfy `UserRead` response contract.
    """
    current_user = ctx.user
    # Normalize current_user to dict (supports both dict and ORM/attr objects)
    if isinstance(current_user, dict):
        user_id = current_user.get("id")
        username = current_user.get("username")
        has_profile_fields = {"name", "profile_image_url"}.issubset(current_user.keys())
    else:
        user_id = getattr(current_user, "id", None)
        username = getattr(current_user, "username", None)
        has_profile_fields = hasattr(current_user, "name") and hasattr(current_user, "profile_image_url")

    # R5-1: Reuse current_user directly if it already has the full profile fields
    # (e.g. if loaded via API key or DB fallback)
    if has_profile_fields:
        return current_user

    try:
        if user_id is not None:
            user = await service.get(int(user_id))
            if user:
                return user
        elif username:
            user = await service.get_by_username(username)
            if user:
                return user
    except (ValueError, TypeError) as e:
        from src.app.core.logger import get_logger

        logger = get_logger(__name__)
        logger.warning("Invalid user context inside read_users_me", error=str(e))
    raise HTTPException(status_code=401, detail="Invalid user context")


@router.get("/{user_id}", response_model=UserReadPublic)
async def read_user(
    request: Request,
    user_id: str,
    ctx: RequestContextDep,
    service: UserService = Depends(_get_user_service),
) -> Any:
    """Get user by ID or username (authenticated users only).

    Supports both integer user IDs and username strings for backward compatibility.
    """
    # Try to get by ID first, then fall back to username
    try:
        user = await service.get(int(user_id))
    except (ValueError, TypeError):
        # If not a valid integer, try username
        user = await service.get_by_username(user_id)

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # SECURITY: tenant isolation — a cross-tenant user is indistinguishable
    # from a nonexistent one (404, not 403). ctx.require_tenant() enforces
    # that this is a tenant-bound path.
    ctx.require_tenant()
    user_tenant_id = user.get("tenant_id") if isinstance(user, dict) else getattr(user, "tenant_id", None)
    if str(user_tenant_id) != str(ctx.tenant_id):
        raise HTTPException(status_code=404, detail="User not found")

    # SECURITY: IDOR via the single ownership seam (superuser bypass included).
    target_user_id = user.get("id") if isinstance(user, dict) else user.id
    ctx.require_ownership(target_user_id)

    return user


@router.patch("/{user_id}", response_model=UserUpdateResponse)
async def patch_user(
    request: Request,
    values: UserUpdate,
    user_id: str,
    ctx: RequestContextDep,
    service: UserService = Depends(_get_user_service),
) -> Any:
    """Update a user (partial update).

    Supports both integer user IDs and username strings for backward compatibility.
    """
    result = await service.update(user_id, values, ctx.user)
    return UserUpdateResponse(message=result.get("message", "User updated successfully"))


@router.put("/{user_id}", response_model=UserUpdateResponse)
async def put_user(
    request: Request,
    values: UserUpdate,
    user_id: str,
    ctx: RequestContextDep,
    service: UserService = Depends(_get_user_service),
) -> Any:
    """Update a user (full update, alias for PATCH).

    Supports both integer user IDs and username strings for backward compatibility.
    """
    result = await service.update(user_id, values, ctx.user)
    return UserUpdateResponse(message=result.get("message", "User updated successfully"))


@router.delete("/{user_id}", response_model=UserDeleteResponse)
async def erase_user(
    request: Request,
    user_id: str,
    ctx: RequestContextDep,
    service: UserService = Depends(_get_user_service),
    token: str = Depends(oauth2_scheme),
) -> Any:
    """Soft delete a user.

    Supports both integer user IDs and username strings for backward compatibility.
    """
    result = await service.delete(user_id, ctx.user, token)
    return UserDeleteResponse(message=result.get("message", "User deleted successfully"))


@router.delete("/db/{user_id}", response_model=UserDeleteResponse, dependencies=[Depends(get_current_superuser)])
async def erase_db_user(
    request: Request,
    user_id: str,
    service: UserService = Depends(_get_user_service),
    token: str = Depends(oauth2_scheme),
) -> UserDeleteResponse:
    """Permanently delete a user from database (superuser only).

    Supports both integer user IDs and username strings for backward compatibility.
    """
    # R1-15: typed DTO instead of dict[str, str] so the response contract is
    # explicit in the OpenAPI schema.
    return UserDeleteResponse(**await service.hard_delete(user_id, token))


@router.get("/{user_id}/rate-limits", response_model=dict[str, Any], dependencies=[Depends(get_current_superuser)])
async def read_user_rate_limits(
    request: Request, user_id: str, service: UserService = Depends(_get_user_service)
) -> dict[str, Any]:
    """Get user's rate limits based on their tier.

    Supports both integer user IDs and username strings for backward compatibility.
    """
    return await service.get_with_rate_limits(user_id)


@router.get("/{user_id}/tier", response_model=UserTierResponse | None)
async def read_user_tier(
    request: Request,
    user_id: str,
    ctx: RequestContextDep,
    service: UserService = Depends(_get_user_service),
) -> Any:
    """Get user's tier information (authenticated users only).

    Supports both integer user IDs and username strings for backward compatibility.
    """
    # SECURITY: IDOR via the single ownership seam — the path may carry an
    # int id or a username; check both against the principal.
    from src.app.core.utils.user_utils import get_user_attr

    principal_id = str(ctx.user_id)
    principal_username = str(get_user_attr(ctx.user, "username"))
    if str(user_id) != principal_id and str(user_id) != principal_username:
        ctx.require_ownership(user_id)

    result = await service.get_with_tier(user_id)
    if result is None:
        return None
    return UserTierResponse(
        tier_id=result.get("tier_id"),
        tier_name=result.get("tier_name"),
        tier_price=result.get("tier_price"),
        tier_currency=result.get("tier_currency"),
    )


@router.patch("/{user_id}/tier", response_model=UserUpdateResponse, dependencies=[Depends(get_current_superuser)])
async def patch_user_tier(
    request: Request, user_id: str, values: UserTierUpdate, service: UserService = Depends(_get_user_service)
) -> UserUpdateResponse:
    """Update user's tier (superuser only).

    Supports both integer user IDs and username strings for backward compatibility.
    """
    # R1-15: typed DTO instead of dict[str, str].
    return UserUpdateResponse(**await service.update_tier(user_id, values))
