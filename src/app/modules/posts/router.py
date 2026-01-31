"""Post API router - HTTP handlers delegating to service layer."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastcrud import PaginatedListResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import get_current_superuser, get_current_user
from ...core.db.database import async_get_db
from ...core.utils.cache import cache
from .schemas import PostCreate, PostRead, PostUpdate
from .service import PostService, get_post_service

router = APIRouter(tags=["posts"])


async def _get_post_service(db: AsyncSession = Depends(async_get_db)) -> PostService:
    """Dependency for PostService injection."""
    return get_post_service(db)


@router.post("/{username}/post", response_model=PostRead, status_code=201)
async def write_post(
    request: Request,
    username: str,
    post: PostCreate,
    current_user: Annotated[dict, Depends(get_current_user)],
    service: PostService = Depends(_get_post_service),
) -> dict[str, Any]:
    """Create a new post."""
    return await service.create_post(post, current_user)


@router.get("/{username}/posts", response_model=PaginatedListResponse[PostRead])
@cache(
    key_prefix="{username}_posts:page_{page}:items_per_page:{items_per_page}",
    resource_id_name="username",
    expiration=60,
)
async def read_posts(
    request: Request,
    username: str,
    current_user: Annotated[dict, Depends(get_current_user)],
    service: PostService = Depends(_get_post_service),
    page: int = 1,
    items_per_page: int = 10,
) -> dict:
    """Get paginated list of posts for a user."""
    return await service.get_posts(current_user["id"], page, items_per_page)


@router.get("/{username}/post/{id}", response_model=PostRead)
@cache(key_prefix="{username}_post_cache", resource_id_name="id")
async def read_post(
    request: Request,
    username: str,
    id: int,
    current_user: Annotated[dict, Depends(get_current_user)],
    service: PostService = Depends(_get_post_service),
) -> dict[str, Any]:
    """Get a single post by ID."""
    return await service.get_post(id, current_user["id"])


@router.patch("/{username}/post/{id}")
@cache("{username}_post_cache", resource_id_name="id", pattern_to_invalidate_extra=["{username}_posts:*"])
async def patch_post(
    request: Request,
    username: str,
    id: int,
    values: PostUpdate,
    current_user: Annotated[dict, Depends(get_current_user)],
    service: PostService = Depends(_get_post_service),
) -> dict[str, str]:
    """Update a post."""
    return await service.update_post(id, values, current_user)


@router.delete("/{username}/post/{id}")
@cache("{username}_post_cache", resource_id_name="id", to_invalidate_extra={"{username}_posts": "{username}"})
async def erase_post(
    request: Request,
    username: str,
    id: int,
    current_user: Annotated[dict, Depends(get_current_user)],
    service: PostService = Depends(_get_post_service),
) -> dict[str, str]:
    """Soft delete a post."""
    return await service.delete_post(id, current_user)


@router.delete("/{username}/db_post/{id}", dependencies=[Depends(get_current_superuser)])
@cache("{username}_post_cache", resource_id_name="id", to_invalidate_extra={"{username}_posts": "{username}"})
async def erase_db_post(
    request: Request, username: str, id: int, service: PostService = Depends(_get_post_service)
) -> dict[str, str]:
    """Permanently delete a post from database (superuser only)."""
    return await service.hard_delete(id)
