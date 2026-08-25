"""Post API router - HTTP handlers delegating to service layer."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastcrud import PaginatedListResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.dependencies import AuthenticatedUser, get_current_superuser, get_current_user
from src.app.core.db.database import async_get_db
from src.app.core.schemas import MessageResponse
from src.app.core.utils.cache import cache
from src.app.core.utils.user_utils import get_user_attr
from src.app.modules.posts.schemas import PostCreate, PostRead, PostUpdate
from src.app.modules.posts.service import PostService, get_post_service

router = APIRouter(tags=["posts"])


async def _get_post_service(db: AsyncSession = Depends(async_get_db)) -> PostService:
    """Dependency for PostService injection."""
    return get_post_service(db)


@router.post("/{username}/posts", response_model=PostRead, status_code=status.HTTP_201_CREATED)
async def write_post(
    request: Request,
    username: str,
    post: PostCreate,
    current_user: Annotated[AuthenticatedUser | Any, Depends(get_current_user)],
    service: PostService = Depends(_get_post_service),
) -> dict[str, Any]:
    """Create a new post."""
    if get_user_attr(current_user, "username") != username:
        from src.app.core.exceptions.http_exceptions import ForbiddenException

        raise ForbiddenException()
    return await service.create_post(post, current_user)


@router.get("/{username}/posts", response_model=PaginatedListResponse[PostRead])
@cache(
    key_prefix="user_posts:{username}:page_{page}:per_page_{per_page}",
    resource_id_name="username",
    expiration=60,
    user_scoped=True,
    # P4: register every cached list page under this pattern so PATCH/DELETE
    # can invalidate via the O(1) pattern-index instead of SCAN.
    invalidation_patterns=["user_posts:{username}:*"],
)
async def read_posts(
    request: Request,
    username: str,
    current_user: Annotated[AuthenticatedUser | Any, Depends(get_current_user)],
    service: PostService = Depends(_get_post_service),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(10, ge=1, le=100, description="Page size"),
) -> Any:
    """Get paginated list of posts for a user."""
    if get_user_attr(current_user, "username") != username:
        from src.app.core.exceptions.http_exceptions import ForbiddenException

        raise ForbiddenException()
    return await service.get_posts(get_user_attr(current_user, "id"), page, per_page)


@router.get("/{username}/posts/{id}", response_model=PostRead)
@cache(key_prefix="user_post_cache:{username}", resource_id_name="id", user_scoped=True)
async def read_post(
    request: Request,
    username: str,
    id: int,
    current_user: Annotated[AuthenticatedUser | Any, Depends(get_current_user)],
    service: PostService = Depends(_get_post_service),
) -> dict[str, Any]:
    """Get a single post by ID."""
    if get_user_attr(current_user, "username") != username:
        from src.app.core.exceptions.http_exceptions import ForbiddenException

        raise ForbiddenException()
    return await service.get_post(id, get_user_attr(current_user, "id"))


@router.patch("/{username}/posts/{id}", response_model=MessageResponse)
@cache(
    "user_post_cache:{username}",
    resource_id_name="id",
    pattern_to_invalidate_extra=["user_posts:{username}:*"],
    user_scoped=True,
)
async def patch_post(
    request: Request,
    username: str,
    id: int,
    values: PostUpdate,
    current_user: Annotated[AuthenticatedUser | Any, Depends(get_current_user)],
    service: PostService = Depends(_get_post_service),
) -> dict[str, str]:
    """Update a post."""
    if get_user_attr(current_user, "username") != username:
        from src.app.core.exceptions.http_exceptions import ForbiddenException

        raise ForbiddenException()
    return await service.update_post(id, values, current_user)


@router.delete("/{username}/posts/{id}", status_code=status.HTTP_204_NO_CONTENT)
@cache(
    "user_post_cache:{username}",
    resource_id_name="id",
    pattern_to_invalidate_extra=["user_posts:{username}:*"],
    user_scoped=True,
)
async def erase_post(
    request: Request,
    username: str,
    id: int,
    current_user: Annotated[AuthenticatedUser | Any, Depends(get_current_user)],
    service: PostService = Depends(_get_post_service),
) -> Response:
    """Soft delete a post."""
    if get_user_attr(current_user, "username") != username:
        from src.app.core.exceptions.http_exceptions import ForbiddenException

        raise ForbiddenException()
    await service.delete_post(id, current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/{username}/db_posts/{id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(get_current_superuser)],
)
@cache(
    "user_post_cache:{username}",
    resource_id_name="id",
    pattern_to_invalidate_extra=["user_posts:{username}:*"],
    user_scoped=True,
)
async def erase_db_post(
    request: Request, username: str, id: int, service: PostService = Depends(_get_post_service)
) -> Response:
    """Permanently delete a post from database (superuser only)."""
    await service.hard_delete(id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
