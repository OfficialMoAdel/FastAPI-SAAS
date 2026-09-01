"""Post API router - HTTP handlers delegating to service layer."""

from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastcrud import PaginatedListResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.deps import RequestContextDep, get_current_superuser
from src.app.core.db.database import async_get_db
from src.app.core.schemas import MessageResponse
from src.app.core.utils.cache import cache
from src.app.core.utils.user_utils import get_user_attr
from src.app.modules.posts.schemas import PostCreate, PostRead, PostUpdate
from src.app.modules.posts.service import PostService, get_post_service

# R1-08: posts previously mounted a top-level path param (/{username}/posts)
# directly under /v1, the only router to do so. That made {username} match any
# first segment and risked shadowing future /v1/<x>/posts routes. The resource
# now lives under a /posts prefix; {username} is nested under /by-user so it no
# longer occupies the top level. The param is retained (cache keys + ownership
# guard reference it) even though it must equal the authenticated user.
router = APIRouter(prefix="/posts", tags=["posts"])


async def _get_post_service(db: AsyncSession = Depends(async_get_db)) -> PostService:
    """Dependency for PostService injection."""
    return get_post_service(db)


@router.post("/by-user/{username}", response_model=PostRead, status_code=status.HTTP_201_CREATED)
async def write_post(
    request: Request,
    username: str,
    post: PostCreate,
    ctx: RequestContextDep,
    service: PostService = Depends(_get_post_service),
) -> dict[str, Any]:
    """Create a new post."""
    if get_user_attr(ctx.user, "username") != username:
        from src.app.core.exceptions.http_exceptions import ForbiddenException

        raise ForbiddenException()
    return await service.create_post(post, ctx.user)


@router.get("/by-user/{username}", response_model=PaginatedListResponse[PostRead])
@cache(
    key_prefix="user_posts:{username}:page_{page}:per_page_{per_page}",
    resource_id_name="username",
    expiration=60,
    user_scoped=True,
    # P4: register every cached list page under this pattern so PATCH/DELETE
    invalidation_patterns=["user_posts:{username}:*"],
)
async def read_posts(
    request: Request,
    username: str,
    ctx: RequestContextDep,
    service: PostService = Depends(_get_post_service),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(10, ge=1, le=100, description="Page size"),
) -> Any:
    """Get paginated list of posts for a user."""
    if get_user_attr(ctx.user, "username") != username:
        from src.app.core.exceptions.http_exceptions import ForbiddenException

        raise ForbiddenException()
    return await service.get_posts(get_user_attr(ctx.user, "id"), page, per_page)


@router.get("/by-user/{username}/{id}", response_model=PostRead)
@cache(key_prefix="user_post_cache:{username}", resource_id_name="id", user_scoped=True)
async def read_post(
    request: Request,
    username: str,
    id: int,
    ctx: RequestContextDep,
    service: PostService = Depends(_get_post_service),
) -> dict[str, Any]:
    """Get a single post by ID."""
    if get_user_attr(ctx.user, "username") != username:
        from src.app.core.exceptions.http_exceptions import ForbiddenException

        raise ForbiddenException()
    return await service.get_post(id, get_user_attr(ctx.user, "id"))


@router.patch("/by-user/{username}/{id}", response_model=MessageResponse)
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
    ctx: RequestContextDep,
    service: PostService = Depends(_get_post_service),
) -> dict[str, str]:
    """Update a post."""
    if get_user_attr(ctx.user, "username") != username:
        from src.app.core.exceptions.http_exceptions import ForbiddenException

        raise ForbiddenException()
    return await service.update_post(id, values, ctx.user)


@router.delete("/by-user/{username}/{id}", status_code=status.HTTP_204_NO_CONTENT)
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
    ctx: RequestContextDep,
    service: PostService = Depends(_get_post_service),
) -> Response:
    """Soft delete a post."""
    if get_user_attr(ctx.user, "username") != username:
        from src.app.core.exceptions.http_exceptions import ForbiddenException

        raise ForbiddenException()
    await service.delete_post(id, ctx.user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/by-user/{username}/db/{id}",
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
