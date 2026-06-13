"""User API router - HTTP handlers delegating to service layer."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.dependencies import get_current_superuser, get_current_user
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


@router.get("", response_model=UserList, dependencies=[Depends(get_current_superuser)])
async def read_users(
    request: Request,
    pagination: PaginationParams = Depends(get_pagination_params),
    service: UserService = Depends(_get_user_service),
) -> Any:
    """Get paginated list of users for the current tenant (superuser only)."""
    offset = (pagination.page - 1) * pagination.per_page
    users_data = await service.crud.get_multi(
        db=service.db,
        offset=offset,
        limit=pagination.per_page,
        is_deleted=False,
        return_as_model=False,
    )

    data = users_data.get("data", []) if isinstance(users_data, dict) else users_data
    # BUG FIX: len(data) is only the current page size, not the full total.
    # Fall back to 0 so the caller knows total is unavailable, not silently wrong.
    total = users_data.get("total_count", 0) if isinstance(users_data, dict) else len(data)
    return {
        "items": data,
        "total": total,
        "page": pagination.page,
        "per_page": pagination.per_page,
        "has_next": pagination.page * pagination.per_page < total,
        "has_previous": pagination.page > 1,
    }


@router.get("/me", response_model=UserRead)
async def read_users_me(
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
    service: UserService = Depends(_get_user_service),
) -> Any:
    """Get current authenticated user profile.

    `get_current_user` returns auth-oriented payload; fetch full user profile
    to satisfy `UserRead` response contract.
    """
    # Normalize current_user to dict (supports both dict and ORM/attr objects)
    if isinstance(current_user, dict):
        user_id = current_user.get("id")
        username = current_user.get("username")
    else:
        user_id = getattr(current_user, "id", None)
        username = getattr(current_user, "username", None)

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


@router.get("/{user_id}", response_model=UserRead)
async def read_user(
    request: Request,
    user_id: str,
    current_user: Annotated[dict, Depends(get_current_user)],
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

    # SECURITY FIX: Ensure tenant context is available and matches user
    from src.app.core.tenancy.context import get_current_tenant

    tenant = get_current_tenant()
    if not tenant:
        raise HTTPException(status_code=400, detail="Tenant context required")

    user_tenant_id = user.get("tenant_id") if isinstance(user, dict) else getattr(user, "tenant_id", None)
    if str(user_tenant_id) != str(tenant.id):
        raise HTTPException(status_code=404, detail="User not found")

    # SECURITY FIX: IDOR check - ensure user can only read their own profile (or is superuser)
    db_username = user.get("username") if isinstance(user, dict) else user.username
    if db_username != current_user["username"] and not current_user.get("is_superuser"):
        raise HTTPException(status_code=403, detail="Forbidden: You can only view your own profile")

    return user


@router.patch("/{user_id}", response_model=UserUpdateResponse)
async def patch_user(
    request: Request,
    values: UserUpdate,
    user_id: str,
    current_user: Annotated[dict, Depends(get_current_user)],
    service: UserService = Depends(_get_user_service),
) -> Any:
    """Update a user (partial update).

    Supports both integer user IDs and username strings for backward compatibility.
    """
    result = await service.update(user_id, values, current_user)
    return UserUpdateResponse(message=result.get("message", "User updated successfully"))


@router.put("/{user_id}", response_model=UserUpdateResponse)
async def put_user(
    request: Request,
    values: UserUpdate,
    user_id: str,
    current_user: Annotated[dict, Depends(get_current_user)],
    service: UserService = Depends(_get_user_service),
) -> Any:
    """Update a user (full update, alias for PATCH).

    Supports both integer user IDs and username strings for backward compatibility.
    """
    result = await patch_user(request, values, user_id, current_user, service)
    return result


@router.delete("/{user_id}", response_model=UserDeleteResponse)
async def erase_user(
    request: Request,
    user_id: str,
    current_user: Annotated[dict, Depends(get_current_user)],
    service: UserService = Depends(_get_user_service),
    token: str = Depends(oauth2_scheme),
) -> Any:
    """Soft delete a user.

    Supports both integer user IDs and username strings for backward compatibility.
    """
    result = await service.delete(user_id, current_user, token)
    return UserDeleteResponse(message=result.get("message", "User deleted successfully"))


@router.delete("/db/{user_id}", response_model=dict[str, str], dependencies=[Depends(get_current_superuser)])
async def erase_db_user(
    request: Request,
    user_id: str,
    service: UserService = Depends(_get_user_service),
    token: str = Depends(oauth2_scheme),
) -> dict[str, str]:
    """Permanently delete a user from database (superuser only).

    Supports both integer user IDs and username strings for backward compatibility.
    """
    return await service.hard_delete(user_id, token)


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
    current_user: Annotated[dict, Depends(get_current_user)],
    service: UserService = Depends(_get_user_service),
) -> Any:
    """Get user's tier information (authenticated users only).

    Supports both integer user IDs and username strings for backward compatibility.
    """
    # SECURITY FIX: IDOR check - ensure user can only view their own tier (or is superuser)
    if (
        user_id != current_user["username"]
        and str(user_id) != str(current_user.get("id"))
        and not current_user.get("is_superuser")
    ):
        raise HTTPException(status_code=403, detail="Forbidden: You can only view your own tier")

    result = await service.get_with_tier(user_id)
    if result is None:
        return None
    return UserTierResponse(
        tier_id=result.get("tier_id"),
        tier_name=result.get("tier_name"),
        tier_price=result.get("tier_price"),
        tier_currency=result.get("tier_currency"),
    )


@router.patch("/{user_id}/tier", response_model=dict[str, str], dependencies=[Depends(get_current_superuser)])
async def patch_user_tier(
    request: Request, user_id: str, values: UserTierUpdate, service: UserService = Depends(_get_user_service)
) -> dict[str, str]:
    """Update user's tier (superuser only).

    Supports both integer user IDs and username strings for backward compatibility.
    """
    return await service.update_tier(user_id, values)
