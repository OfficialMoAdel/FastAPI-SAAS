"""
RBAC Permission Dependency for FastAPI.

Provides permission checking via requires_permission() factory.
Uses bitmask integers for O(1) permission lookups with minimal Redis memory.

Bitmask Strategy:
- Each permission maps to a bit position (0-63)
- User's permissions stored as single integer in Redis
- Permission check: (user_mask & permission_bit) != 0
- Multiple role masks combined with OR
"""

import asyncio
from collections.abc import Callable
from typing import Any
from uuid import UUID

from cachetools import TTLCache
from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.cache.l1 import L1Cache, PairKeyCodec
from src.app.core.db.scoped import execute_scoped
from src.app.core.logger import get_logger
from src.app.core.rbac.bitmask import PermissionBitmask
from src.app.core.rbac.models import require_permission_graph
from src.app.core.rbac.permissions import Permission as PermissionEnum
from src.app.core.rbac.superuser_tenant import enforce_superuser_tenant_rule
from src.app.core.tenancy.scope import ExplicitTenant
from src.app.shared.exceptions import ForbiddenError as ForbiddenException
from src.app.shared.exceptions import UnauthorizedError as UnauthorizedException

logger = get_logger(__name__)

# Cache TTL for user permissions (5 minutes)
PERMISSIONS_CACHE_TTL = 300
# P1-7: singleflight registry for bitmask recomputation. Concurrent cache
# misses on the same (tenant, user) key share one 4-table join instead of
# each firing their own; the winner alone performs the DB query + setex.
_in_flight_bitmask: dict[str, asyncio.Task[int]] = {}
_bitmask_lock = asyncio.Lock()

# R1-OPT: process-local bitmask L1 (short TTL by design). Same staleness
# argument as the auth-info L1: Redis DELETE on role change is the synchronous
# happy path, so 30s bounds only the cross-process pub/sub gap — not the
# 300s Redis TTL. Keyed on (user_id, tenant_id); the permission version hash
# is a process-constant (_PERMISSION_VERSION_HASH), so deploys (new process =
# empty L1) are the only version transitions and need no key component.
_BITMASK_L1_TTL = 30.0
_BITMASK_L1_MAXSIZE = 2048
_BITMASK_L1_INVALIDATION_CHANNEL = "perms:bitmask:l1_invalidate"
_bitmask_l1: TTLCache[tuple[int, str], int] = TTLCache(maxsize=_BITMASK_L1_MAXSIZE, ttl=_BITMASK_L1_TTL)


def _bitmask_l1_key(user_id: int, tenant_id: int | UUID) -> tuple[int, str]:
    return (int(user_id), str(tenant_id))


# The seam owns the grammar. PairKeyCodec is the fail-SAFE codec: an unparseable
# payload broadcasts, because it must not leave a revoked permission cached.
# "" broadcasts too, matching the original handler. The codec also preserves a
# subtle asymmetry — a bare digit like "5" parses to the key (5, "") and matches
# nothing, rather than clearing the cache.
_bitmask_l1_seam = L1Cache(
    name="bitmask_l1",
    channel=_BITMASK_L1_INVALIDATION_CHANNEL,
    codec=PairKeyCodec(empty_is_broadcast=True),
    caches=[_bitmask_l1],
    logger_name="bitmask_l1",
)

async def _publish_bitmask_l1_invalidation(user_id: int, tenant_id: int | UUID) -> None:
    await _bitmask_l1_seam.publish(f"{int(user_id)}:{tenant_id}")


async def get_user_permissions(
    user_id: int,
    tenant_id: int | UUID,
    db: AsyncSession,
) -> set[str]:
    """
    Fetch all permission slugs for a user within a specific tenant.
    Queries UserRole -> Role -> RolePermission -> Permission.
    """
    # Row types from the application seam; see core/rbac/models.py.
    graph = require_permission_graph()
    stmt = (
        select(graph.permission.slug)
        .join(graph.role_permission, graph.role_permission.permission_id == graph.permission.id)
        .join(graph.role, graph.role.id == graph.role_permission.role_id)
        .join(graph.user_role, graph.user_role.role_id == graph.role.id)
        .where(graph.user_role.user_id == user_id)
        .limit(1000)  # Safety bound
    )
    result = await execute_scoped(
        stmt,
        db=db,
        model=graph.user_role,
        scope=ExplicitTenant(tenant_id, reason='legacy-criterion:UserRole'),
    )
    return set(result.scalars().all())


def _get_bitmask_cache_key(user_id: int, tenant_id: int | UUID) -> str:
    """Get Redis key for bitmask-based permission cache."""
    # Append version hash to invalidate cache when permissions change (e.g. new deployment)
    version = PermissionBitmask.get_permission_version_hash()
    return f"perms:bitmask:{tenant_id}:{user_id}:{version}"


async def get_cached_permissions_bitmask(
    user_id: int,
    tenant_id: int | UUID,
    db: AsyncSession,
) -> int:
    """
    Get user permissions as bitmask with Redis caching.

    Benefits over Redis Sets:
    - Single integer vs set of 20+ strings (lower memory)
    - Single GET vs SMEMBERS (faster retrieval)
    - Bitwise operations for permission checks (no network round-trip per check)

    Args:
        user_id: The user's ID
        tenant_id: The tenant's ID
        db: Database session for fallback query

    Returns:
        Integer bitmask with bits set for each permission
    """
    from src.app.core.redis_provider import get_redis

    l1_key = _bitmask_l1_key(user_id, tenant_id)
    hit = _bitmask_l1.get(l1_key)
    if hit is not None:
        return hit

    redis_client = get_redis()

    cache_key = _get_bitmask_cache_key(user_id, tenant_id)
    if redis_client is not None:
        try:
            cached = await redis_client.get(cache_key)
            if cached is not None:
                # Decode bytes to int
                mask = int(cached.decode() if isinstance(cached, bytes) else cached)
                _bitmask_l1[l1_key] = mask
                return mask
        except Exception as e:
            logger.warning(
                "Failed to read bitmask from cache",
                user_id=user_id,
                tenant_id=tenant_id,
                error=str(e),
            )

    # Query from database — singleflight: concurrent misses on this key
    # share one join; only the winner queries + backfills the cache.
    async with _bitmask_lock:
        task = _in_flight_bitmask.get(cache_key)
        if task is None:
            task = asyncio.create_task(_compute_and_cache_bitmask(user_id, tenant_id, db, redis_client, cache_key))
            _in_flight_bitmask[cache_key] = task
    try:
        return await task
    finally:
        async with _bitmask_lock:
            if _in_flight_bitmask.get(cache_key) is task:
                _in_flight_bitmask.pop(cache_key, None)


async def _compute_and_cache_bitmask(
    user_id: int,
    tenant_id: int | UUID,
    db: AsyncSession,
    redis_client: Any,
    cache_key: str,
) -> int:
    """Run the 4-table permission join and backfill the bitmask cache.

    Only the singleflight winner runs this; concurrent waiters await its
    task and receive the identical mask.
    """
    permissions = await get_user_permissions(user_id, tenant_id, db)

    # Encode to bitmask
    mask = PermissionBitmask.encode(permissions)

    # Cache the bitmask
    if redis_client is not None:
        try:
            await redis_client.setex(cache_key, PERMISSIONS_CACHE_TTL, str(mask))
        except Exception as e:
            logger.warning(
                "Failed to cache bitmask",
                user_id=user_id,
                tenant_id=tenant_id,
                error=str(e),
            )
    # Writer-visible immediately: L1 backfill even when Redis is down.
    _bitmask_l1[_bitmask_l1_key(user_id, tenant_id)] = mask

    return mask


async def has_permission_cached(
    user_id: int,
    tenant_id: int | UUID,
    permission_slug: str,
    db: AsyncSession,
) -> bool:
    """
    Check if a user has a specific permission using O(1) bitmask check.

    Uses cached bitmask for pure in-memory bitwise check after initial Redis GET.
    This is more efficient than Redis SISMEMBER as it:
    - Retrieves single integer vs checking membership
    - Enables multiple permission checks without additional network calls
    - Uses less Redis memory (single integer vs set of strings)

    Args:
        user_id: The user's ID
        tenant_id: The tenant's ID
        permission_slug: The permission to check (e.g., "users:read")
        db: Database session for fallback query

    Returns:
        True if user has the permission, False otherwise
    """
    # Get user's permission bitmask (from cache or DB)
    mask = await get_cached_permissions_bitmask(user_id, tenant_id, db)

    # O(1) bitwise check: (mask & permission_bit) != 0
    return PermissionBitmask.has_permission(mask, permission_slug)


def requires_permission(permission: PermissionEnum | str) -> Callable[..., Any]:
    """
    FastAPI dependency factory that checks if the current user has a specific permission.

    Usage:
        from src.app.core.rbac import Permission, requires_permission

        @router.get("/roles", dependencies=[Depends(requires_permission(Permission.ROLES_READ))])
        async def list_roles():
            ...

    Logic:
        1. Superusers bypass all permission checks
        2. Check Redis cache for user's permissions
        3. If not cached, query DB and cache the result
        4. Raise 403 Forbidden if permission not found
    """
    permission_slug = permission.value if isinstance(permission, PermissionEnum) else permission

    from src.app.core.db.database import async_get_db
    from src.app.core.principal import AuthContext, DenialReason, denial, get_auth_context
    from src.app.core.tenancy.context import resolve_tenant_id

    async def _check_permission(
        request: Request = None,  # type: ignore[assignment]
        db: AsyncSession = Depends(async_get_db),
        auth_context: AuthContext = Depends(get_auth_context),
    ) -> None:
        # Single production path: tests inject a real AuthContext via
        # dependency_overrides (tests/helpers/auth.py). No token fallback,
        # no mock sniffing, no spoofed identities.
        #
        # ``get_auth_context`` comes from ``core.principal``, not
        # ``api.deps``: it is the same dependency object, but core no longer
        # reaches into the api layer to obtain it (H-5).
        current_user = auth_context.user
        tenant_context = auth_context.tenant

        if tenant_context:
            # The principal's own tenant is authoritative — ADR-0006's
            # "context wins" rule. It is read from the resolved principal
            # rather than re-read from the ContextVar because the principal
            # IS the DB-validated result of that resolution.
            tenant_id = tenant_context.id
        else:
            # No tenant on the principal (absent context, or the falsy
            # NO_TENANT sentinel on an exempt path). A ``{tenant_id}`` path
            # param is an EXPLICIT value handed to the ONE resolution seam
            # (ADR-0006) — never a second resolver, and never a reason to
            # import the Tenant model. The path's transport type (``str``) is
            # normalized to the domain type (``UUID``) here, before it crosses
            # the seam, so downstream cache keys and scoped queries keep
            # seeing a UUID exactly as they did before.
            path_tenant: Any = request.path_params.get("tenant_id") if request is not None else None
            if isinstance(path_tenant, str):
                try:
                    path_tenant = UUID(path_tenant)
                except ValueError:
                    pass
            tenant_id = resolve_tenant_id(
                path_tenant,
                entity_name="Permission",
                entity_id=permission_slug,
                fail_closed=False,
            )
        if tenant_id is None:
            # SECURITY FIX: never fall back to a default tenant ID. One
            # no-tenant contract (P3-#14): TenantContextError — the same class
            # ``tenant_dependency`` and ``RequestContext.require_tenant()``
            # raise — so every no-tenant seam speaks one status (403).
            raise denial(DenialReason.TENANT_CONTEXT_MISSING)

        is_superuser = current_user.is_superuser
        user_id_val = current_user.id

        if user_id_val is None:
            raise UnauthorizedException("User ID is required.")
        user_id = int(user_id_val)
        # Enforce tenant isolation before any permission bypass. Superusers may bypass
        # permission checks within their tenant, but they cannot act as another tenant.
        # Applies to every principal — test-injected AuthContexts included, so HTTP
        # tests exercise the same isolation as production.
        # P1: this is now the SHARED rule, so this gate and
        # get_current_superuser cannot drift apart again.
        enforce_superuser_tenant_rule(
            user_tenant_id=current_user.tenant_id,
            request_tenant=tenant_id,
            user_id=user_id,
            gate="requires_permission",
        )

        if is_superuser:
            logger.info(
                "Superuser bypass used",
                user_id=user_id,
                tenant_id=str(tenant_id),
                permission=permission_slug,
            )
            return

        # Check permission using O(1) bitmask (uses pre-fetched bitmask from request.state if available)
        cached_tuple = getattr(request.state, "cached_permissions_bitmask", None) if request is not None else None
        if cached_tuple is not None and cached_tuple[0] == user_id and str(cached_tuple[1]) == str(tenant_id):
            mask = cached_tuple[2]
            has_perm = PermissionBitmask.has_permission(mask, permission_slug)
            if not has_perm:
                # A mask that denies is NOT a denial. The composite auth prefetch
                # (core/security/auth_prefetch.py) reads
                # perms:bitmask:{tenant}:{user}:{version} and yields 0 on a cache
                # MISS rather than consulting the database, so "mask is zero" is
                # indistinguishable from "this user has no permissions" while
                # meaning the opposite -- "nobody has computed this mask yet".
                # Confirm the denial against the DB-backed path before rejecting.
                #
                # Measured (bead FastAPI_saas-nymp): with this fallback absent, a
                # cold bitmask key made every RBAC-gated endpoint fail closed
                # (403 "Permission denied: users:read") for every user in every
                # environment, while the DB-backed call returned True for the
                # same user and a spy recorded zero calls to it. The DB-backed
                # path is itself cached (Redis bitmask plus an in-process L1
                # map), so this only costs a lookup when the prefetch denies --
                # the allow fast path above is unchanged.
                has_perm = await has_permission_cached(user_id, tenant_id, permission_slug, db)
        else:
            has_perm = await has_permission_cached(user_id, tenant_id, permission_slug, db)

        # Raise if permission denied
        if not has_perm:
            raise ForbiddenException(f"Permission denied: {permission_slug}")

    return _check_permission


async def invalidate_user_permissions_cache(user_id: int, tenant_id: int | UUID) -> None:
    """
    Invalidate the cached permissions for a user in a specific tenant.
    Clears both Set-based cache and bitmask cache for backward compatibility.
    Call this when user roles are modified.
    """
    from src.app.core.redis_provider import get_redis

    _bitmask_l1.pop(_bitmask_l1_key(user_id, tenant_id), None)
    await _publish_bitmask_l1_invalidation(user_id, tenant_id)

    redis_client = get_redis()

    if redis_client is None:
        return
    bitmask_cache_key = _get_bitmask_cache_key(user_id, tenant_id)

    try:
        # Delete cache
        await redis_client.delete(bitmask_cache_key)
    except Exception as e:
        # Log cache invalidation failure
        logger.warning(
            "Failed to invalidate permissions cache",
            user_id=user_id,
            tenant_id=tenant_id,
            error=str(e),
        )


async def invalidate_users_permissions_cache_batch(user_ids: list[int], tenant_id: int | UUID) -> None:
    """
    Invalidate the cached permissions for multiple users in a specific tenant in a single pipeline.
    Call this when a role assigned to many users is modified.
    """
    if not user_ids:
        return

    from src.app.core.redis_provider import get_redis

    # Drop every local L1 entry FIRST, so this process never serves a
    # stale cached mask for any of the users it is invalidating. Pub/sub
    # is cross-process and would not echo back to us, so the local drops
    # have to lead the fan-out below.
    for user_id in user_ids:
        _bitmask_l1.pop(_bitmask_l1_key(user_id, tenant_id), None)

    redis_client = get_redis()

    if redis_client is None:
        return

    try:
        # Invalidate the per-user Redis bitmask keys in a single pipeline
        # round-trip (one RTT for all users).
        async with redis_client.pipeline(transaction=False) as pipe:
            for user_id in user_ids:
                bitmask_cache_key = _get_bitmask_cache_key(user_id, tenant_id)
                pipe.delete(bitmask_cache_key)
            await pipe.execute()

        # Fan the L1 invalidation messages out as a SINGLE publish round-trip
        # (one execute()), while preserving input order so subscribers see the
        # payloads in the same order the caller passed the users. Dropping
        # locally first and publishing afterwards keeps the cross-worker L1
        # invalidation invariant (invalidate.py docstring) and makes the L1
        # seam's per-message wiring a pure fan-out instead of a per-user
        # serialize/publish loop.
        async with redis_client.pipeline(transaction=False) as pipe:
            for user_id in user_ids:
                pipe.publish(
                    _BITMASK_L1_INVALIDATION_CHANNEL,
                    f"{int(user_id)}:{tenant_id}",
                )
            await pipe.execute()
    except Exception as e:
        logger.warning(
            "Failed to batch invalidate permissions cache",
            user_count=len(user_ids),
            tenant_id=tenant_id,
            error=str(e),
        )
