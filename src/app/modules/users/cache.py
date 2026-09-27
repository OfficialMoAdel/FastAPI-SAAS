"""User caching and cache invalidation infrastructure.

Collaborator for UserService (extracted per PR-05 / Finding #7).
Handles:
- User profile cache key generation (tenant-scoped and global)
- Profile caching and deserialization
- Single-user cache invalidation with token-version bump verification
- Batch-user cache invalidation in pipelined Redis operations
"""

from __future__ import annotations

import copy
from collections.abc import Sequence
from typing import Any

from cachetools import TTLCache
from structlog import get_logger

from src.app.core.cache.invalidation_exhaustion import (
    ExhaustionMarker,
    InvalidationPolicy,
    invalidate_with_exhaustion,
)
from src.app.core.cache.l1 import L1Cache, RawKeyCodec
from src.app.core.json_utils import json_dumps, json_loads
from src.app.core.redis_provider import get_redis
from src.app.core.tenancy.context import get_current_tenant
from src.app.core.utils.cache_guard import cache_guard
from src.app.modules.users.cache_keys import (  # noqa: F401  (re-exported)
    AUTH_INFO_CACHE_PREFIX,
    USER_CACHE_PREFIX,
    _user_auth_cache_key,
    _user_cache_key,
    _user_email_cache_key,
    _user_username_cache_key,
)
from src.app.modules.users.models import User
from src.app.modules.users.session_invalidation import (
    InvalidationReason,
    build_user_invalidation_keys,
    should_bump_token_version,
)

logger = get_logger(__name__)


# Cache configuration
USER_CACHE_TTL = 300  # seconds (P-M6: raised from 60 — invalidation is already
# wired on every user mutation via UserCacheInvalidator, so the TTL is only a
# staleness backstop, not the freshness mechanism; 60s was paying a DB read
# per user profile every minute for no correctness gain)
AUTH_INFO_CACHE_TTL = 3600  # 1 hour

#: The user key family's DECLARED exhaustion policy. A stale auth-info entry
#: keeps authorizing a revoked user, so exhaustion leaves a poison-key marker
#: for the (parked, dashboard-side) reader. Flipping the family to log-only
#: is a one-line change here.
_USER_INVALIDATION_POLICY = InvalidationPolicy(
    family="User",
    on_exhaustion=ExhaustionMarker.POISON_KEY,
    exhausted_message="User cache invalidation failed after retry — stale auth info may persist until TTL",
)


def _user_cache_poison_key(user_ids: Sequence[Any]) -> str:
    """Marker key for a batch whose invalidation exhausted its retries.

    Mirrors the single-user marker (``user:invalidation_failed:<id>``) with a
    batch-scoped suffix, so a later reader can find the affected entities
    without the two paths colliding.
    """
    if len(user_ids) == 1:
        return f"user:invalidation_failed:{user_ids[0]}"
    digest = str(abs(hash(tuple(sorted(str(u) for u in user_ids)))))
    return f"user:invalidation_failed:batch:{len(user_ids)}:{digest}"


async def _write_batch_poison_marker(client: Any, marker_key: str) -> None:
    """Write the batch poison marker, bounded by the same TTL as the single path."""
    await client.set(marker_key, "batch", ex=AUTH_INFO_CACHE_TTL)

# R1-OPT: process-local auth-info L1 (short TTL by design). The Redis TTL
# (3600s) is only a staleness BACKSTOP — happy-path invalidation is a
# synchronous Redis DELETE, so effective staleness is ~0. A 300s L1 keyed off
# the same TTL would add a new stale window (Redis deleted, L1 still serving
# revoked/is_active/token_version payloads). 8s keeps the hot-user RTT win
# while bounding that window to the negative-cache discipline already
# accepted for bans/IP status (5s).
_AUTH_INFO_L1_TTL = 8.0
_AUTH_INFO_L1_MAXSIZE = 2048
_AUTH_INFO_L1_INVALIDATION_CHANNEL = "user_auth:l1_invalidate"
_auth_info_l1: TTLCache[str, dict[str, Any]] = TTLCache(maxsize=_AUTH_INFO_L1_MAXSIZE, ttl=_AUTH_INFO_L1_TTL)

# I11: constructing the seam self-registers with the central L1 registry, so the
# lifespan still starts/stops every listener without importing owner modules
# one-by-one. The grammar that used to live in `_on_auth_info_l1_invalidation` —
# "" and "*" both mean "drop everything", anything else is a cache key — is now
# declared once in src/app/core/cache/l1.py.
_auth_info_l1_seam = L1Cache(
    name="auth_info_l1",
    channel=_AUTH_INFO_L1_INVALIDATION_CHANNEL,
    codec=RawKeyCodec(empty_is_broadcast=True),
    caches=[_auth_info_l1],
    logger_name="user_auth_l1",
)


class UserCacheInvalidator:
    """Collaborator for user profile and auth info caching and invalidation."""

    user_cache_key = staticmethod(_user_cache_key)
    user_email_cache_key = staticmethod(_user_email_cache_key)
    user_username_cache_key = staticmethod(_user_username_cache_key)
    user_auth_cache_key = staticmethod(_user_auth_cache_key)

    @staticmethod
    async def get_cached_user(cache_key: str) -> dict[str, Any] | None:
        """Get cached user data if available (L1 auth-info short-circuit + Redis)."""
        # L1 only covers auth-info keys — profile/email/username keys keep the
        # existing Redis-only path (identical keys, no new staleness class).
        if cache_key.startswith(f"{AUTH_INFO_CACHE_PREFIX}:"):
            hit = _auth_info_l1.get(cache_key)
            if hit is not None:
                # Deep copy: auth payloads nest dicts (tier); callers mutate
                # the returned dict and shared nested refs would pollute L1.
                return copy.deepcopy(hit)
        client = get_redis()
        if client is None:
            return None

        with cache_guard("user_get", cache_key=cache_key):
            cached = await client.get(cache_key)
            if cached:
                result: dict[str, Any] = json_loads(cached)
                if cache_key.startswith(f"{AUTH_INFO_CACHE_PREFIX}:"):
                    _auth_info_l1[cache_key] = copy.deepcopy(result)
                return result
        return None

    @staticmethod
    async def cache_user(user: User | dict[str, Any]) -> None:
        """Cache user profile in Redis."""
        client = get_redis()
        if client is None:
            return

        user_id_log = user["id"] if isinstance(user, dict) else getattr(user, "id", "unknown")
        with cache_guard("user_write", user_id=str(user_id_log)):
            # Handle both ORM object and dict
            if isinstance(user, dict):
                user_data = user.copy()
                user_data.pop("hashed_password", None)
                user_id = user["id"]
                email = user["email"]
                username = user["username"]
                tenant_id = user.get("tenant_id")
            else:
                # U7: Direct dict construction instead of Pydantic validate+dump
                # ORM data is already validated, skip redundant Pydantic overhead
                user_data = {
                    "id": user.id,
                    "created_at": user.created_at.isoformat() if user.created_at else None,
                    "updated_at": user.updated_at.isoformat() if user.updated_at else None,
                    "name": user.name,
                    "username": user.username,
                    "email": user.email,
                    "profile_image_url": user.profile_image_url,
                    "tier_id": user.tier_id,
                    "tenant_id": str(user.tenant_id) if user.tenant_id else None,
                    "is_superuser": user.is_superuser,
                    "is_email_verified": user.is_email_verified,
                    "is_active": user.is_active,
                }
                user_id = user.id
                email = user.email
                username = user.username
                tenant_id = user.tenant_id

            serialized = json_dumps(user_data)

            # Cache by all lookup keys using a pipeline for single round-trip
            async with client.pipeline(transaction=False) as pipe:
                pipe.setex(_user_cache_key(user_id), USER_CACHE_TTL, serialized)
                pipe.setex(_user_email_cache_key(email, tenant_id), USER_CACHE_TTL, serialized)
                pipe.setex(_user_username_cache_key(username, tenant_id), USER_CACHE_TTL, serialized)
                await pipe.execute()

    @staticmethod
    async def cache_user_auth_info(cache_key: str, user_with_tier: dict[str, Any]) -> None:
        """Cache user auth info in Redis (and refresh the L1 entry)."""
        # Writer-process visibility is synchronous: populate L1 here so this
        # process never serves a stale entry it just overwrote.
        _auth_info_l1[cache_key] = copy.deepcopy(user_with_tier)
        client = get_redis()
        if client is None:
            return

        with cache_guard("auth_info_write", cache_key=cache_key):
            serialized = json_dumps(user_with_tier)
            await client.setex(cache_key, AUTH_INFO_CACHE_TTL, serialized)

    @staticmethod
    async def invalidate_user_cache(
        user: User | dict[str, Any],
        bump_token_version: bool,
        reason: InvalidationReason,
        explicit_token_version: int | None = None,
    ) -> None:
        """Invalidate all cache entries for a user.

        Q-M8 (mirrored from tenants): invalidation failure is NOT a benign
        cache miss — a stale ``is_active=True`` / old ``token_version``
        survives in the auth-info cache and keeps authorizing a revoked
        user for up to AUTH_INFO_CACHE_TTL. Failures are retried exactly
        once, then logged at error with a poison-key marker so a later
        reader/ops sweep can detect the user whose cache could not be
        invalidated. That retry / visibility / marker policy is NOT written
        here: it is the shared exhaustion policy in
        ``core.cache.invalidation_exhaustion``, and this family's marker
        decision is declared on the policy row below.

        1B: when ``bump_token_version`` is True the caller MUST supply
        ``explicit_token_version`` (the fresh post-bump value owned by the
        ``invalidate_user_sessions`` seam). Guessing ``old + 1`` from the
        possibly-stale ``user`` payload silently diverges Redis from the DB
        under concurrent bumps — route through the seam instead.
        """
        if bump_token_version != should_bump_token_version(reason):
            raise ValueError(
                f"bump_token_version={bump_token_version} does not match "
                f"should_bump_token_version(reason={reason!r}). "
                "Pass the bump value derived from the reason."
            )
        client = get_redis()
        if client is None:
            _auth_info_l1.clear()
            return

        if bump_token_version and explicit_token_version is None:
            raise ValueError(
                "bump_token_version=True requires explicit_token_version. "
                "Do not guess old+1 from a possibly-stale user payload — route "
                "through src.app.modules.users.session_invalidation.invalidate_user_sessions, "
                "which supplies the fresh post-bump version."
            )

        try:
            # Handle both ORM object and dict
            if isinstance(user, dict):
                user_id = user["id"]
                email = user["email"]
                username = user["username"]
                tenant_id = user.get("tenant_id")
            else:
                user_id = user.id
                email = user.email
                username = user.username
                tenant_id = user.tenant_id

            # Scope resolution lives in build_user_invalidation_keys, which
            # builds both key families and must not see them disagree.
            keys, token_keys = build_user_invalidation_keys(user_id, username, email, tenant_id)
            poison_key = f"user:invalidation_failed:{user_id}"
            # Synchronous local drop: this process must not serve the entry it
            # just deleted from Redis (pub/sub is cross-process only).
            for key in keys:
                if key.startswith(f"{AUTH_INFO_CACHE_PREFIX}:"):
                    # Drop locally, then fan out — the seam owns that ordering.
                    await _auth_info_l1_seam.invalidate(key)

            async def _invalidate_redis() -> None:
                if keys:
                    await client.delete(*keys)
                if token_keys:
                    if explicit_token_version is not None:
                        new_version = explicit_token_version
                    else:
                        # bump_token_version=True without an explicit version
                        # raises above; reaching here means no bump, so the
                        # current version is written through unchanged.
                        if isinstance(user, dict):
                            new_version = user.get("token_version", 0)
                        else:
                            new_version = getattr(user, "token_version", 0)

                    async with client.pipeline(transaction=False) as pipe:
                        for key in token_keys:
                            pipe.set(key, str(new_version), ex=300)
                        await pipe.execute()

            async def _write_poison_marker() -> None:
                # AUTH_INFO_CACHE_TTL bounds the marker exactly as it bounds
                # the stale auth-info entry (ADR-0004: this card changed
                # failure policy only, not the TTL).
                await client.set(poison_key, str(reason), ex=AUTH_INFO_CACHE_TTL)

            await invalidate_with_exhaustion(
                _invalidate_redis,
                policy=_USER_INVALIDATION_POLICY,
                log_context={"user_id": str(user_id), "poison_key": poison_key},
                write_marker=_write_poison_marker,
            )
        except Exception as e:
            user_id_log = user["id"] if isinstance(user, dict) else user.id
            logger.warning("Failed to invalidate cache for user", user_id_log=user_id_log, error=e)

    @staticmethod
    async def invalidate_user_cache_batch(
        users: Sequence[Any],
        bump_token_version: bool = False,
        reason: InvalidationReason | None = None,
        explicit_token_versions: dict[Any, int] | None = None,
    ) -> None:
        """Invalidate cache entries for many users in ONE Redis pipeline.

        1B: when ``bump_token_version`` is True and ``explicit_token_versions``
        maps user id -> fresh post-bump version (supplied by the
        ``invalidate_user_sessions`` seam), the explicit value wins over the
        ``old + 1`` fallback so concurrent bumps cannot diverge Redis from
        the DB.
        """
        if reason is not None and bump_token_version != should_bump_token_version(reason):
            raise ValueError(
                f"bump_token_version={bump_token_version} does not match should_bump_token_version(reason={reason!r})."
            )
        if not users:
            return
        client = get_redis()
        if client is None:
            _auth_info_l1.clear()
            return

        tenant_context = get_current_tenant()
        delete_keys: list[str] = []
        token_sets: list[tuple[str, int, bool]] = []
        batch_ids: list[Any] = []

        for user in users:
            try:
                if isinstance(user, dict):
                    user_id = user["id"]
                    email = user["email"]
                    username = user["username"]
                    tenant_id = user.get("tenant_id")
                    current_version = user.get("token_version", 0)
                elif hasattr(user, "id"):
                    user_id = user.id
                    email = user.email
                    username = user.username
                    tenant_id = user.tenant_id
                    current_version = getattr(user, "token_version", 0)
                else:
                    user_id = getattr(user, "id", user[0] if len(user) > 0 else None)
                    email = getattr(user, "email", user[1] if len(user) > 1 else None)
                    username = getattr(user, "username", user[2] if len(user) > 2 else None)
                    tenant_id = getattr(user, "tenant_id", user[3] if len(user) > 3 else None)
                    current_version = getattr(user, "token_version", user[4] if len(user) > 4 else 0)

                if tenant_id is not None:
                    batch_ids.append(user_id)

                if tenant_id is None and tenant_context is not None:
                    tenant_id = tenant_context.tenant_id

                # Guard non-str/None values and empty strings since batch inputs
                # may be raw tuples or partial dicts. Lowercasing/stripping is
                # handled by build_user_invalidation_keys.
                norm_email = str(email).strip() or None if email is not None else None
                norm_username = str(username).strip() or None if username is not None else None
                if user_id is None:
                    logger.warning("Skipping batch-invalidation entry without user id")
                    continue

                u_del_keys, u_tok_keys = build_user_invalidation_keys(user_id, norm_username, norm_email, tenant_id)
                delete_keys.extend(u_del_keys)
                for tk in u_tok_keys:
                    has_explicit = (
                        bump_token_version
                        and explicit_token_versions is not None
                        and user_id in explicit_token_versions
                    )
                    if has_explicit:
                        # Seam-supplied fresh post-bump value: already resolved,
                        # must NOT be incremented again below.
                        token_sets.append((tk, explicit_token_versions[user_id], True))  # type: ignore[index]
                    else:
                        token_sets.append((tk, current_version, False))
            except Exception as e:
                logger.warning("Failed to build batch-invalidation keys for user", error=e)

        # Synchronous local drop + cross-worker publish for auth-info keys only.
        for key in delete_keys:
            if key.startswith(f"{AUTH_INFO_CACHE_PREFIX}:"):
                await _auth_info_l1_seam.invalidate(key)
        if not delete_keys and not token_sets:
            return

        async def _batch_invalidate_redis() -> None:
            async with client.pipeline(transaction=False) as pipe:
                if delete_keys:
                    pipe.delete(*delete_keys)
                for key, version, is_explicit in token_sets:
                    new_version = version if is_explicit else (version + 1 if bump_token_version else version)
                    pipe.set(key, str(new_version), ex=300)
                await pipe.execute()

        # The batch path is a security-relevant mutation (RBAC role
        # assign/remove routes here), so it runs under the SAME declared
        # policy as the single-user path: one retry, an error that names what
        # stays stale, and the family's poison marker. A bare try/except left
        # batch invalidation strictly LESS safe than single invalidation of
        # the same data, and reintroduced in the module that owns the family
        # the divergence core/cache/invalidation_exhaustion.py exists to
        # eliminate.
        marker_key = _user_cache_poison_key(batch_ids)
        await invalidate_with_exhaustion(
            _batch_invalidate_redis,
            policy=_USER_INVALIDATION_POLICY,
            log_context={
                "count": len(users),
                "user_ids": [str(u) for u in batch_ids[:20]],
                "poison_key": marker_key,
            },
            write_marker=lambda: _write_batch_poison_marker(client, marker_key),
        )


__all__ = [
    "AUTH_INFO_CACHE_PREFIX",
    "AUTH_INFO_CACHE_TTL",
    "USER_CACHE_PREFIX",
    "USER_CACHE_TTL",
    "UserCacheInvalidator",
    "_auth_info_l1",
    "_user_auth_cache_key",
    "_user_cache_key",
    "_user_email_cache_key",
    "_user_username_cache_key",
]
