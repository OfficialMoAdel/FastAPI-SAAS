"""Post API router - HTTP handlers delegating to service layer."""

from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastcrud import PaginatedListResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.deps import RequestContextDep, get_current_superuser
from src.app.core.db.database import async_get_db
from src.app.core.logger import get_logger
from src.app.core.schemas import MessageResponse
from src.app.core.tenancy.context import get_current_tenant
from src.app.core.utils.cache import cache
from src.app.core.utils.cache.tenant_index import invalidate_tenant_cache
from src.app.modules.posts import PostService
from src.app.modules.posts.schemas import PostCreate, PostRead, PostUpdate
from src.app.shared.exceptions import ForbiddenError as ForbiddenException

logger = get_logger(__name__)

# R1-08: posts previously mounted a top-level path param (/{username}/posts)
# directly under /v1, the only router to do so. That made {username} match any
# first segment and risked shadowing future /v1/<x>/posts routes. The resource
# now lives under a /posts prefix; {username} is nested under /by-user so it no
# longer occupies the top level. The param is retained (cache keys + ownership
# guard reference it) even though it must equal the authenticated user.
router = APIRouter(prefix="/posts", tags=["posts"])

def _require_post_owner(username: str, ctx: RequestContextDep) -> None:
    """Enforce that the authenticated user matches the path's username."""
    if ctx.user.username != username:
        raise ForbiddenException()


def get_post_service(db: AsyncSession = Depends(async_get_db)) -> PostService:
    """Dependency injection factory for PostService.

    Q-M4: factories live in router.py — service files stay Depends-free.
    (Absorbs the old pointless `_get_post_service` wrapper, M-4.)
    """
    return PostService(db)

@router.post("/by-user/{username}", response_model=PostRead, status_code=status.HTTP_201_CREATED)
async def write_post(
    request: Request,
    username: str,
    post: PostCreate,
    ctx: RequestContextDep,
    service: PostService = Depends(get_post_service),
) -> dict[str, Any]:
    """Create a new post."""
    _require_post_owner(username, ctx)
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
    service: PostService = Depends(get_post_service),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(10, ge=1, le=100, description="Page size"),
) -> Any:
    """Get paginated list of posts for a user."""
    _require_post_owner(username, ctx)
    return await service.get_posts(ctx.user.id, page, per_page)


@router.get("/by-user/{username}/{id}", response_model=PostRead)
@cache(key_prefix="user_post_cache:{username}", resource_id_name="id", user_scoped=True)
async def read_post(
    request: Request,
    username: str,
    id: int,
    ctx: RequestContextDep,
    service: PostService = Depends(get_post_service),
) -> dict[str, Any]:
    """Get a single post by ID."""
    _require_post_owner(username, ctx)
    return await service.get_post(id, ctx.user.id)


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
    service: PostService = Depends(get_post_service),
) -> dict[str, str]:
    """Update a post."""
    _require_post_owner(username, ctx)
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
    service: PostService = Depends(get_post_service),
) -> Response:
    """Soft delete a post."""
    _require_post_owner(username, ctx)
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
    # Run-3 finding cache.posts_superuser_hard_delete_invalidates_wrong_key.
    # Deliberately NOT user_scoped: this route deletes ANOTHER user's post, so
    # there is no caller identity whose user-scoped key could ever match the
    # owner's read entry. The old user_scoped=True + no-principal handler made
    # the invalidation a silent no-op and the post stayed readable for the full
    # TTL. Erasure is now driven explicitly by the tenant index below, which is
    # identity-agnostic by construction.
    user_scoped=False,
)
async def erase_db_post(
    request: Request, username: str, id: int, service: PostService = Depends(get_post_service)
) -> Response:
    """Permanently delete a post from database (superuser only)."""
    await service.hard_delete(id)

    # Identity-agnostic erasure: drop every cached copy of this post for the
    # tenant, whichever user it was cached under. The tenant index holds all
    # `tenant:`-prefixed keys, so this is O(|tenant entries|) and needs no
    # knowledge of the owner's id.
    #
    # invalidate_tenant_cache() already no-ops when the cache client is absent,
    # so no client check is needed here.
    tenant = get_current_tenant()
    if tenant is not None:
        try:
            await invalidate_tenant_cache(
                str(tenant.tenant_id), prefix=f"v2:user_post_cache:{username}:{id}"
            )
        except Exception as exc:  # noqa: BLE001 - erasure must not 500 the takedown
            logger.warning(
                "superuser_post_erase_cache_invalidation_failed",
                username=username,
                post_id=id,
                error=str(exc),
            )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
