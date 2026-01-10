"""User API router - HTTP handlers delegating to service layer."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import get_current_superuser, get_current_user
from ...core.db.database import async_get_db
from ...core.security import oauth2_scheme
from .crud import crud_users
from .schemas import UserCreate, UserList, UserRead, UserTierUpdate, UserUpdate
from .service import user_service

router = APIRouter(tags=["users"])


@router.post("/user", response_model=UserRead, status_code=201)
async def write_user(
    request: Request, user: UserCreate, db: Annotated[AsyncSession, Depends(async_get_db)]
) -> dict[str, Any]:
    """Create a new user."""
    return await user_service.create_user(user, db)


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
    request: Request, username: str, db: Annotated[AsyncSession, Depends(async_get_db)]
) -> dict[str, Any]:
    """Get user by username (authenticated users only)."""
    return await user_service.get_user(username, db)


@router.patch("/user/{username}")
async def patch_user(
    request: Request,
    values: UserUpdate,
    username: str,
    current_user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict[str, str]:
    """Update a user."""
    return await user_service.update_user(username, values, current_user, db)


@router.delete("/user/{username}")
async def erase_user(
    request: Request,
    username: str,
    current_user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
    token: str = Depends(oauth2_scheme),
) -> dict[str, str]:
    """Soft delete a user."""
    return await user_service.delete_user(username, current_user, token, db)


@router.delete("/db_user/{username}", dependencies=[Depends(get_current_superuser)])
async def erase_db_user(
    request: Request,
    username: str,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    token: str = Depends(oauth2_scheme),
) -> dict[str, str]:
    """Permanently delete a user from database (superuser only)."""
    return await user_service.hard_delete_user(username, token, db)


@router.get("/user/{username}/rate_limits", dependencies=[Depends(get_current_superuser)])
async def read_user_rate_limits(
    request: Request, username: str, db: Annotated[AsyncSession, Depends(async_get_db)]
) -> dict[str, Any]:
    """Get user's rate limits based on their tier."""
    return await user_service.get_user_with_rate_limits(username, db)


@router.get("/user/{username}/tier", dependencies=[Depends(get_current_user)])
async def read_user_tier(
    request: Request, username: str, db: Annotated[AsyncSession, Depends(async_get_db)]
) -> dict | None:
    """Get user's tier information (authenticated users only)."""
    return await user_service.get_user_with_tier(username, db)


@router.patch("/user/{username}/tier", dependencies=[Depends(get_current_superuser)])
async def patch_user_tier(
    request: Request, username: str, values: UserTierUpdate, db: Annotated[AsyncSession, Depends(async_get_db)]
) -> dict[str, str]:
    """Update user's tier (superuser only)."""
    return await user_service.update_user_tier(username, values, db)
