"""User API router - HTTP handlers delegating to service layer."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.status import HTTP_404_NOT_FOUND

from ...api.dependencies import get_current_superuser, get_current_user
from ...core.db.database import async_get_db
from ...core.pagination import PaginationParams, get_pagination_params
from ...core.security import oauth2_scheme
from .schemas import (
    UserCreate,
    UserDeleteResponse,
    UserList,
    UserRead,
    UserTierResponse,
    UserTierUpdate,
    UserUpdate,
    UserUpdateResponse,
)
from .service import UserService, get_user_service

router = APIRouter(prefix="/users", tags=["users"])


async def _get_username_from_id(service: UserService, user_id: str) -> str:
    """Helper to get username from ID, handling both ORM objects and cached dicts."""
    user = await service.get(int(user_id))
    if not user:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="User not found")
    return user["username"] if isinstance(user, dict) else user.username


async def _get_user_service(db: AsyncSession = Depends(async_get_db)) -> UserService:
    """Dependency for UserService injection."""
    return get_user_service(db)


@router.post("", response_model=UserRead, status_code=201, dependencies=[Depends(get_current_user)])
async def write_user(request: Request, user: UserCreate, service: UserService = Depends(_get_user_service)) -> Any:
    """Create a new user."""
    return await service.create(user)


@router.get("", response_model=UserList, dependencies=[Depends(get_current_superuser)])
async def read_users(
    request: Request,
    pagination: PaginationParams = Depends(get_pagination_params),
    service: UserService = Depends(_get_user_service),
) -> dict:
    """Get paginated list of users for the current tenant (superuser only)."""
    users_data = await service.list_users(page=pagination.page, per_page=pagination.per_page, is_deleted=False)

    data = users_data.get("data", [])
    total = users_data.get("total_count", len(data))
    return {
        "items": data,
        "total": total,
        "page": pagination.page,
        "per_page": pagination.per_page,
        "has_next": pagination.page * pagination.per_page < total,
        "has_previous": pagination.page > 1,
    }


@router.get("/me", response_model=UserRead)
async def read_users_me(request: Request, current_user: Annotated[dict, Depends(get_current_user)]) -> dict:
    """Get current authenticated user."""
    return current_user


@router.get("/{user_id}", response_model=UserRead, dependencies=[Depends(get_current_user)])
async def read_user(request: Request, user_id: str, service: UserService = Depends(_get_user_service)) -> Any:
    """Get user by ID or username (authenticated users only).

    Supports both integer user IDs and username strings for backward compatibility.
    """
    # Try to get by ID first, then fall back to username
    try:
        return await service.get(int(user_id))
    except (ValueError, KeyError):
        # If not a valid integer or not found, try username
        return await service.get_by_username(user_id)


@router.patch("/{user_id}", response_model=UserUpdateResponse)
async def patch_user(
    request: Request,
    values: UserUpdate,
    user_id: str,
    current_user: Annotated[dict, Depends(get_current_user)],
    service: UserService = Depends(_get_user_service),
) -> UserUpdateResponse:
    """Update a user (partial update).

    Supports both integer user IDs and username strings for backward compatibility.
    """
    # Try to get by ID first, then fall back to username
    try:
        username = await _get_username_from_id(service, user_id)
    except (ValueError, KeyError):
        username = user_id

    result = await service.update(username, values, current_user)
    return UserUpdateResponse(message=result.get("message", "User updated successfully"))


@router.put("/{user_id}", response_model=UserUpdateResponse)
async def put_user(
    request: Request,
    values: UserUpdate,
    user_id: str,
    current_user: Annotated[dict, Depends(get_current_user)],
    service: UserService = Depends(_get_user_service),
) -> UserUpdateResponse:
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
) -> UserDeleteResponse:
    """Soft delete a user.

    Supports both integer user IDs and username strings for backward compatibility.
    """
    # Try to get by ID first, then fall back to username
    try:
        username = await _get_username_from_id(service, user_id)
    except (ValueError, KeyError):
        username = user_id

    await service.delete(username, current_user, token)
    return UserDeleteResponse()


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
    # Try to get by ID first, then fall back to username
    try:
        username = await _get_username_from_id(service, user_id)
    except (ValueError, KeyError):
        username = user_id

    return await service.hard_delete(username, token)


@router.get("/{user_id}/rate-limits", response_model=dict[str, Any], dependencies=[Depends(get_current_superuser)])
async def read_user_rate_limits(
    request: Request, user_id: str, service: UserService = Depends(_get_user_service)
) -> dict[str, Any]:
    """Get user's rate limits based on their tier.

    Supports both integer user IDs and username strings for backward compatibility.
    """
    # Try to get by ID first, then fall back to username
    try:
        username = await _get_username_from_id(service, user_id)
    except (ValueError, KeyError):
        username = user_id

    return await service.get_with_rate_limits(username)


@router.get("/{user_id}/tier", response_model=UserTierResponse | None, dependencies=[Depends(get_current_user)])
async def read_user_tier(
    request: Request, user_id: str, service: UserService = Depends(_get_user_service)
) -> UserTierResponse | None:
    """Get user's tier information (authenticated users only).

    Supports both integer user IDs and username strings for backward compatibility.
    """
    # Try to get by ID first, then fall back to username
    try:
        username = await _get_username_from_id(service, user_id)
    except (ValueError, KeyError):
        username = user_id

    result = await service.get_with_tier(username)
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
    # Try to get by ID first, then fall back to username
    try:
        username = await _get_username_from_id(service, user_id)
    except (ValueError, KeyError):
        username = user_id

    return await service.update_tier(username, values)
