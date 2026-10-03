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
    """Create a new post.

    S-33 FIX: this route carried no cache decorator, so creating a post never
    invalidated the `user_posts:{username}:*` list cache that `read_posts`
    fills — a new post stayed invisible until those entries aged out (60s).
    `patch_post` and `erase_post` both invalidated that list; only the create
    path was missed.

    The invalidation is explicit rather than via `@cache(...,
    pattern_to_invalidate_extra=...)` because this route has no resource id:
    `@cache` always infers one and raises `CacheIdentificationInferenceError`
    when it cannot. A create endpoint has no prior cache entry for the new
    resource, so only the LIST pattern needs dropping — which is exactly what
    `invalidate_by_pattern` does.

    THE PATTERN MUST BE SCOPED. This is the part that made the first attempt a
    silent no-op: `read_posts` registers each list page under
    `_pattern_index_key(_scoped_invalidation_pattern(...))`, so the index entry
    is namespaced `tenant:{id}:user:{uid}:user_posts:{username}:*`. Passing the
    bare pattern here read a DIFFERENT, always-empty Redis set and returned 0.
    The write was safe and the list was never dropped.

    Fail-soft, matching the decorator: a cache error must not fail the write.
    """
    _require_post_owner(username, ctx)
    result = await service.create_post(post, ctx.user)

    from src.app.core.utils.cache.decorator import _format_prefix, _scoped_invalidation_pattern
    from src.app.core.utils.cache.patterns import invalidate_by_pattern

    try:
        tenant = get_current_tenant()
        # Truthiness, not `is not None`: NO_TENANT is falsy by design, and
        # scoping by its UUID-zero id would name an index nothing registers.
        scoped = _scoped_invalidation_pattern(
            _format_prefix("user_posts:{username}:*", {"username": username}),
            str(tenant.tenant_id) if tenant else None,
            ctx.user.id,
        )
        evicted = await invalidate_by_pattern(scoped)
        if not evicted:
            # 0 is possible but must be visible: this is the exact signature of
            # the bug this replaced.
            logger.info("post_create_evicted_no_cached_list_pages", username=username, pattern=scoped)
    except Exception as e:  # noqa: BLE001 - the write already succeeded
        logger.warning(
            "post_create_cache_invalidation_failed",
            username=username,
            error=str(e),
        )
    return result


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
    # S-14 FIX: the prefix must match what the WRITER shards on.
    # `decorator._build_cache_key` builds the base key as
    # `f"{_CACHE_KEY_VERSION}:{formatted_key_prefix}:{resource_id}"`, but the
    # tenant index is sharded by `_format_prefix(key_prefix, kwargs)` alone —
    # so neither the `v2:` version token nor the trailing `:id` belong in the
    # shard name. Passing them made the shard lookup miss, `invalidate_...`
    # returned 0, and the erasure silently left the cached post live.
    #
    # `invalidate_tenant_cache` now also falls back to the global index on a
    # shard miss, so a wrong prefix degrades to a filtered sweep rather than
    # to nothing.
    try:
        if not tenant:
            # NO_TENANT is falsy by design (S-16). There is no tenant index to
            # sweep here, and there is nothing honest to substitute: entries
            # were written under WHATEVER tenant was bound at read time, so
            # this erasure cannot reach them. Saying so is the point — the
            # previous `if tenant is not None:` test built a UUID-zero id,
            # looked up an index that exists under no real tenant, and
            # silently dropped a takedown on the floor.
            logger.warning(
                "superuser_post_erase_without_tenant_context",
                username=username,
                post_id=id,
                detail=(
                    "row is deleted but cached copies under any real tenant "
                    "index survive until their TTL expires"
                ),
            )
        else:
            evicted = await invalidate_tenant_cache(
                str(tenant.tenant_id), prefix=f"user_post_cache:{username}"
            )
            # S-14: the return value used to be discarded, so a shard miss
            # looked identical to a successful eviction. 0 is legitimately
            # possible (nothing was ever cached), but on an ERASURE it must be
            # observable.
            if not evicted:
                logger.info(
                    "superuser_post_erase_evicted_no_cached_copies",
                    username=username,
                    post_id=id,
                    tenant_id=str(tenant.tenant_id),
                )
    except Exception as exc:  # noqa: BLE001 - erasure must not 500 the takedown
        logger.warning(
            "superuser_post_erase_cache_invalidation_failed",
            username=username,
            post_id=id,
            error=str(exc),
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
