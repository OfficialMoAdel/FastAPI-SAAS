"""User API router - HTTP handlers delegating to service layer."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import get_current_superuser, get_current_user
from ...core.db.database import async_get_db
from ...core.security import oauth2_scheme
from .schemas import UserCreate, UserList, UserRead, UserTierUpdate, UserUpdate
from .service import UserService, get_user_service

router = APIRouter(prefix="/users", tags=["users"])


async def _get_username_from_id(service: UserService, user_id: str) -> str:
    """Helper to get username from ID, handling both ORM objects and cached dicts."""
    user = await service.get(int(user_id))
    if not user:
        raise ValueError("User not found")
    return user["username"] if isinstance(user, dict) else user.username


async def _get_user_service(db: AsyncSession = Depends(async_get_db)) -> UserService:
    """Dependency for UserService injection."""
    return get_user_service(db)


@router.post("", response_model=UserRead, status_code=201)
async def write_user(request: Request, user: UserCreate, service: UserService = Depends(_get_user_service)) -> Any:
    """Create a new user."""
    return await service.create(user)


@router.get("", response_model=UserList, dependencies=[Depends(get_current_superuser)])
async def read_users(
    request: Request,
    page: int = 1,
    per_page: int = 10,
    service: UserService = Depends(_get_user_service),
) -> dict:
    """Get paginated list of users for the current tenant (superuser only)."""
    users_data = await service.list_users(page=page, per_page=per_page, is_deleted=False)

    data = users_data.get("data", [])
    total = users_data.get("total_count", len(data))
    return {
        "items": data,
        "total": total,
        "page": page,
        "per_page": per_page,
        "has_next": page * per_page < total,
        "has_previous": page > 1,
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


@router.patch("/{user_id}")
async def patch_user(
    request: Request,
    values: UserUpdate,
    user_id: str,
    current_user: Annotated[dict, Depends(get_current_user)],
    service: UserService = Depends(_get_user_service),
) -> dict[str, str]:
    """Update a user (partial update).

    Supports both integer user IDs and username strings for backward compatibility.
    """
    # Try to get by ID first, then fall back to username
    try:
        username = await _get_username_from_id(service, user_id)
    except (ValueError, KeyError):
        username = user_id

    return await service.update(username, values, current_user)


@router.put("/{user_id}")
async def put_user(
    request: Request,
    values: UserUpdate,
    user_id: str,
    current_user: Annotated[dict, Depends(get_current_user)],
    service: UserService = Depends(_get_user_service),
) -> dict[str, str]:
    """Update a user (full update, alias for PATCH).

    Supports both integer user IDs and username strings for backward compatibility.
    """
    return await patch_user(request, values, user_id, current_user, service)


@router.delete("/{user_id}")
async def erase_user(
    request: Request,
    user_id: str,
    current_user: Annotated[dict, Depends(get_current_user)],
    service: UserService = Depends(_get_user_service),
    token: str = Depends(oauth2_scheme),
) -> dict[str, str]:
    """Soft delete a user.

    Supports both integer user IDs and username strings for backward compatibility.
    """
    # Try to get by ID first, then fall back to username
    try:
        username = await _get_username_from_id(service, user_id)
    except (ValueError, KeyError):
        username = user_id

    return await service.delete(username, current_user, token)


@router.delete("/db/{user_id}", dependencies=[Depends(get_current_superuser)])
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


@router.get("/{user_id}/rate-limits", dependencies=[Depends(get_current_superuser)])
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


@router.get("/{user_id}/tier", dependencies=[Depends(get_current_user)])
async def read_user_tier(
    request: Request, user_id: str, service: UserService = Depends(_get_user_service)
) -> dict | None:
    """Get user's tier information (authenticated users only).

    Supports both integer user IDs and username strings for backward compatibility.
    """
    # Try to get by ID first, then fall back to username
    try:
        username = await _get_username_from_id(service, user_id)
    except (ValueError, KeyError):
        username = user_id

    return await service.get_with_tier(username)


@router.patch("/{user_id}/tier", dependencies=[Depends(get_current_superuser)])
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
