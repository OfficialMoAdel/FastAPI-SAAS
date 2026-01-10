"""User API router - HTTP handlers delegating to service layer."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import get_current_superuser, get_current_user
from ...core.db.database import async_get_db
from ...core.security import oauth2_scheme
from .crud import crud_users
from .schemas import UserCreate, UserList, UserRead, UserTierUpdate, UserUpdate
from .service import UserService, get_user_service

router = APIRouter(tags=["users"])


async def _get_user_service(db: AsyncSession = Depends(async_get_db)) -> UserService:
    """Dependency for UserService injection."""
    return get_user_service(db)


@router.post("/user", response_model=UserRead, status_code=201)
async def write_user(
    request: Request, user: UserCreate, service: UserService = Depends(_get_user_service)
) -> dict[str, Any]:
    """Create a new user."""
    return await service.create(user)


@router.get("/users", response_model=UserList, dependencies=[Depends(get_current_superuser)])
async def read_users(
    request: Request, db: Annotated[AsyncSession, Depends(async_get_db)], page: int = 1, per_page: int = 10
) -> dict:
    """Get paginated list of users for the current tenant (superuser only)."""
    offset = (page - 1) * per_page
    users_data = await crud_users.get_multi(
        db=db,
        offset=offset,
        limit=per_page,
        is_deleted=False,
    )

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


@router.get("/user/me/", response_model=UserRead)
async def read_users_me(request: Request, current_user: Annotated[dict, Depends(get_current_user)]) -> dict:
    """Get current authenticated user."""
    return current_user


@router.get("/user/{username}", response_model=UserRead, dependencies=[Depends(get_current_user)])
async def read_user(
    request: Request, username: str, service: UserService = Depends(_get_user_service)
) -> dict[str, Any]:
    """Get user by username (authenticated users only)."""
    return await service.get_by_username(username)


@router.patch("/user/{username}")
async def patch_user(
    request: Request,
    values: UserUpdate,
    username: str,
    current_user: Annotated[dict, Depends(get_current_user)],
    service: UserService = Depends(_get_user_service),
) -> dict[str, str]:
    """Update a user."""
    return await service.update(username, values, current_user)


@router.delete("/user/{username}")
async def erase_user(
    request: Request,
    username: str,
    current_user: Annotated[dict, Depends(get_current_user)],
    service: UserService = Depends(_get_user_service),
    token: str = Depends(oauth2_scheme),
) -> dict[str, str]:
    """Soft delete a user."""
    return await service.delete(username, current_user, token)


@router.delete("/db_user/{username}", dependencies=[Depends(get_current_superuser)])
async def erase_db_user(
    request: Request,
    username: str,
    service: UserService = Depends(_get_user_service),
    token: str = Depends(oauth2_scheme),
) -> dict[str, str]:
    """Permanently delete a user from database (superuser only)."""
    return await service.hard_delete(username, token)


@router.get("/user/{username}/rate_limits", dependencies=[Depends(get_current_superuser)])
async def read_user_rate_limits(
    request: Request, username: str, service: UserService = Depends(_get_user_service)
) -> dict[str, Any]:
    """Get user's rate limits based on their tier."""
    return await service.get_with_rate_limits(username)


@router.get("/user/{username}/tier", dependencies=[Depends(get_current_user)])
async def read_user_tier(
    request: Request, username: str, service: UserService = Depends(_get_user_service)
) -> dict | None:
    """Get user's tier information (authenticated users only)."""
    return await service.get_with_tier(username)


@router.patch("/user/{username}/tier", dependencies=[Depends(get_current_superuser)])
async def patch_user_tier(
    request: Request, username: str, values: UserTierUpdate, service: UserService = Depends(_get_user_service)
) -> dict[str, str]:
    """Update user's tier (superuser only)."""
    return await service.update_tier(username, values)
