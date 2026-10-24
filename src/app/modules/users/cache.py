"""User caching and cache invalidation infrastructure.

Collaborator for UserService (extracted per PR-05 / Finding #7).
Handles:
- User profile cache key generation (tenant-scoped and global)
- Profile caching and deserialization
- Single-user cache invalidation with token-version bump verification
- Batch-user cache invalidation in pipelined Redis operations
"""

from __future__ import annotations

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
USER_CACHE_TTL = 300  # seconds (P-M6: raised from 60; invalidation already wired
        # on every mutation, so this is just a staleness backstop)
AUTH_INFO_CACHE_TTL = 3600  # 1 hour

#: User exhaustion policy: stale auth-info keeps authorizing a revoked
#: user, so exhaustion leaves a poison-key marker. Flip to log-only here
#: (one line).
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

# R1-OPT: process-local auth-info L1 (short TTL by design). Redis TTL
# (3600s) is only a backstop — invalidation is a sync DELETE, so staleness
# is ~0. 8s keeps the RTT win while bounding the stale window like bans/IP
# status (5s).
_AUTH_INFO_L1_TTL = 8.0
_AUTH_INFO_L1_MAXSIZE = 2048
_AUTH_INFO_L1_INVALIDATION_CHANNEL = "user_auth:l1_invalidate"
_auth_info_l1: TTLCache[str, dict[str, Any]] = TTLCache(maxsize=_AUTH_INFO_L1_MAXSIZE, ttl=_AUTH_INFO_L1_TTL)

# I11: seam self-registers with the central L1 registry, so the lifespan
# starts/stops all listeners without per-module imports. The drop-everything
# grammar ("" and "*") now lives in src/app/core/cache/l1.py.
_auth_info_l1_seam = L1Cache(
    name="auth_info_l1",
    channel=_AUTH_INFO_L1_INVALIDATION_CHANNEL,
    codec=RawKeyCodec(empty_is_broadcast=True),
    caches=[_auth_info_l1],
    logger_name="user_auth_l1",
)


def _two_level_copy(payload: dict[str, Any]) -> dict[str, Any]:
    """Copy a user auth payload by value: the top level, plus ``tier``.

    ``copy.deepcopy`` walks arbitrary structure; this walks exactly the shape
    :func:`assert_user_payload_shape` admits, which is one level deep with at
    most one nested dict (``tier``) whose own values are all scalars. That is
    why the assertion is load-bearing rather than decorative: it is the
    contract that makes this copy an isomorphism rather than a silent
    shallowing. A non-dict ``tier`` (including ``None``) is carried across
    unchanged, exactly as ``deepcopy`` would carry an immutable across.

    The shape assertion is invoked at every copy site rather than here, so a
    payload that stops conforming fails AT the store or the read, where the
    offending value is in hand.
    """
    copied = dict(payload)
    tier = payload.get("tier")
    if isinstance(tier, dict):
        copied["tier"] = dict(tier)
    return copied


def assert_user_payload_shape(payload: dict[str, Any]) -> None:
    """Fail loudly unless the auth payload is exactly one level deep plus `tier`.

    S-4 oracle guard (permanent, not a one-time test): the two-level copy is
    only isomorphic while the payload stays one level deep with at most one
    nested dict, ``tier``, whose own values are all scalars. If someone later
    adds a nested structure, the fast copy would silently stop copying it, so
    this raises :class:`TypeError` at the copy site instead of passing a test.

    Shape contract (from ``crud.py`` ``get_user_with_tier_eager``): no
    top-level value is a ``dict`` or ``list`` except ``tier``, and no value
    inside ``tier`` is a ``dict`` or ``list``. A non-dict ``tier`` (including
    ``None`` for users without a tier) passes through unchanged.
    """

    for key, value in payload.items():
        if key == "tier":
            continue
        if isinstance(value, dict | list):
            raise TypeError(
                f"user payload key {key!r} holds {type(value).__name__}; "
                "the two-level copy only copies the top level plus 'tier'"
            )
    tier = payload.get("tier")
    if isinstance(tier, dict):
        for nested_key, nested_value in tier.items():
            if isinstance(nested_value, dict | list):
                raise TypeError(
                    f"user payload tier[{nested_key!r}] holds {type(nested_value).__name__}; "
                    "the two-level copy does not copy below 'tier'"
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
        # L1 covers only auth-info keys; profile/email/username stay Redis-only (same keys, no new staleness class).
        if cache_key.startswith(f"{AUTH_INFO_CACHE_PREFIX}:"):
            hit = _auth_info_l1.get(cache_key)
            if hit is not None:
                # Copy by value: payloads nest a dict (tier); a shared nested
                # ref would pollute L1. S-4 oracle: shape guard at every copy
                # site prevents silent shallowing.
                assert_user_payload_shape(hit)
                return _two_level_copy(hit)
        client = get_redis()
        if client is None:
            return None

        with cache_guard("user_get", cache_key=cache_key):
            cached = await client.get(cache_key)
            if cached:
                result: dict[str, Any] = json_loads(cached)
                if cache_key.startswith(f"{AUTH_INFO_CACHE_PREFIX}:"):
                    assert_user_payload_shape(result)
                    _auth_info_l1[cache_key] = _two_level_copy(result)
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
            # ORM object or dict
            if isinstance(user, dict):
                user_data = user.copy()
                user_data.pop("hashed_password", None)
                user_id = user["id"]
                email = user["email"]
                username = user["username"]
                tenant_id = user.get("tenant_id")
            else:
                # U7: direct dict instead of Pydantic validate+dump; ORM data is already validated.
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

            # Cache by all lookup keys via one pipeline round-trip
            async with client.pipeline(transaction=False) as pipe:
                pipe.setex(_user_cache_key(user_id), USER_CACHE_TTL, serialized)
                pipe.setex(_user_email_cache_key(email, tenant_id), USER_CACHE_TTL, serialized)
                pipe.setex(_user_username_cache_key(username, tenant_id), USER_CACHE_TTL, serialized)
                await pipe.execute()

    @staticmethod
    async def cache_user_auth_info(cache_key: str, user_with_tier: dict[str, Any]) -> None:
        """Cache user auth info in Redis (and refresh the L1 entry)."""
        # Writer-process visibility: populate L1 synchronously so this
        # process never serves a stale entry it just wrote. S-4 oracle:
        # shape-checked before storage, so non-conforming payloads fail at
        # the writer.
        assert_user_payload_shape(user_with_tier)
        _auth_info_l1[cache_key] = _two_level_copy(user_with_tier)
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
            # ORM object or dict
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

            # Scope resolution in build_user_invalidation_keys; both key families must agree.
            keys, token_keys = build_user_invalidation_keys(user_id, username, email, tenant_id)
            poison_key = f"user:invalidation_failed:{user_id}"
            # P3-4: drop L1 locally first (sync, free), then let L2 delete
            # land, then publish to peers. Previously one publish per key
            # serialized up to 4 round trips and inverted the order
            # (GATE 93vx). `drop_locally` + `publish_many` sends identical
            # payloads; only round trips and order change.
            pending_publish = _auth_info_l1_seam.drop_locally(
                [key for key in keys if key.startswith(f"{AUTH_INFO_CACHE_PREFIX}:")]
            )

            # Whether the L2 delete LANDED. The exhaustion policy swallows
            # the delete error after retry, so only this flag distinguishes
            # success from failure. Same as the batch path below.
            delete_landed = False

            async def _invalidate_redis() -> None:
                nonlocal delete_landed
                if keys:
                    await client.delete(*keys)
                if token_keys:
                    if explicit_token_version is not None:
                        new_version = explicit_token_version
                    else:
                        # bump=True without explicit version raises above;
                        # reaching here means no bump, so the current version
                        # passes through unchanged.
                        if isinstance(user, dict):
                            new_version = user.get("token_version", 0)
                        else:
                            new_version = getattr(user, "token_version", 0)

                    async with client.pipeline(transaction=False) as pipe:
                        for key in token_keys:
                            pipe.set(key, str(new_version), ex=300)
                        await pipe.execute()
                delete_landed = True

            async def _write_poison_marker() -> None:
                # AUTH_INFO_CACHE_TTL bounds the marker as it bounds the
                # stale auth-info entry (ADR-0004: failure policy changed,
                # TTL unchanged).
                await client.set(poison_key, str(reason), ex=AUTH_INFO_CACHE_TTL)

            await invalidate_with_exhaustion(
                _invalidate_redis,
                policy=_USER_INVALIDATION_POLICY,
                log_context={"user_id": str(user_id), "poison_key": poison_key},
                write_marker=_write_poison_marker,
            )

            # P3-4: peers told LAST, only if delete landed. A failed delete
            # leaves them holding the truth (row never deleted). Telling them
            # anyway drops a still-valid entry — a needless miss, not a bug
            # — but the batch path draws the same line and the two must agree.
            if pending_publish and delete_landed:
                await _auth_info_l1_seam.publish_many(pending_publish)
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

                # Guard non-str/None/empty values; batch inputs may be raw
                # tuples or partial dicts. Lowercasing/stripping is handled
                # by build_user_invalidation_keys.
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
                        # Seam-supplied fresh post-bump value; already resolved, must not be incremented again.
                        token_sets.append((tk, explicit_token_versions[user_id], True))  # type: ignore[index]
                    else:
                        token_sets.append((tk, current_version, False))
            except Exception as e:
                logger.warning("Failed to build batch-invalidation keys for user", error=e)

        if not delete_keys and not token_sets:
            return

        # THE ORDER (GATE 93vx, 2026-10-20): L2 DELETE lands first, L1
        # publish follows. Same shape as
        # src/app/core/rbac/dependencies.py::invalidate_users_permissions_cache_batch
        # — the two now agree.
        #
        # Delete goes first because a publish tells peers to drop their
        # copy, but the row is still in Redis until the delete lands — a peer
        # re-reading in that window re-caches the value being removed. Delete
        # first removes the value peers would re-read.
        #
        # A failed delete means peers are never told and local caches are
        # cleared — nothing stale is served (row never deleted), but
        # invalidation itself failed: the token stays valid. That's
        # security-relevant, surfaced by the exhaustion policy (one retry,
        # then poison marker): LOGS, doesn't raise. The local drop is NOT
        # rolled back — deliberately the safe direction; the failed delete is
        # the loud one.
        #
        # Fan-out stays BATCHED: one pipeline, one round trip instead of one
        # publish per key; payloads keep delete-key build order.
        auth_info_keys = [key for key in delete_keys if key.startswith(f"{AUTH_INFO_CACHE_PREFIX}:")]
        pending_publish = _auth_info_l1_seam.drop_locally(auth_info_keys)

        # Whether the L2 delete LANDED. The exhaustion policy swallows the
        # delete error after retry; only this flag distinguishes success from
        # failure.
        delete_landed = False

        async def _batch_invalidate_redis() -> None:
            nonlocal delete_landed
            async with client.pipeline(transaction=False) as pipe:
                if delete_keys:
                    pipe.delete(*delete_keys)
                for key, version, is_explicit in token_sets:
                    new_version = version if is_explicit else (version + 1 if bump_token_version else version)
                    pipe.set(key, str(new_version), ex=300)
                await pipe.execute()
            delete_landed = True

        # Batch path is security-relevant (RBAC role assign/remove), so it
        # runs under the SAME declared policy as single-user: one retry, an
        # error naming what stays stale, plus the poison marker. A bare
        # try/except left batch strictly LESS safe than single invalidation —
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

        # Peers told LAST, only if delete landed. The exhaustion policy
        # swallows the delete error; only the flag decides — a failed delete
        # leaves peers holding whatever they had.
        if pending_publish and delete_landed:
            await _auth_info_l1_seam.publish_many(pending_publish)


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
