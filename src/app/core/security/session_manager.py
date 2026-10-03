"""Session Manager for concurrent session limiting.

Prevents account sharing by limiting the number of concurrent sessions per user.
Tracks active sessions in Redis with automatic expiration.

Performance Optimization (z28g.3):
- Uses hybrid ZSET + Hash structure for O(log n) eviction
- ZSET (sorted set) for ordering by timestamp
- Hash for session metadata storage
- ZPOPMIN for oldest eviction instead of HGETALL+sort

Performance Optimization (P2-1):
- Uses Redis pipeline for batch eviction operations
- ZRANGE + Pipeline reduces roundtrips by ~86-97%
- Non-destructive ZRANGE followed by atomic ZREM+HDEL
"""

from datetime import UTC, datetime
from typing import Any, cast

from structlog import get_logger

from src.app.core.config import settings
from src.app.core.json_utils import json_dumps, json_loads
from src.app.core.monitoring.r1_unified_metrics import record_session_eviction
from src.app.core.utils.lua_manager import get_lua_manager

logger = get_logger(__name__)


_UNSET = object()
class SessionManager:
    """Manages user sessions for concurrent session limiting.

    Features:
    - Limits concurrent sessions per user (default: 5)
    - Tracks sessions in Redis with TTL
    - Automatically invalidates oldest sessions when limit exceeded
    - O(log n) eviction using ZSET ordering
    """

    # Redis key prefix for legacy sessions (deprecated)
    SESSION_KEY_PREFIX = "user_sessions:"
    # Redis key prefix for user sessions (Hash - stores session metadata)
    SESSION_HASH_PREFIX = "user_sessions_v2:"
    # Redis key prefix for session ordering (ZSET - stores session_id -> timestamp)
    SESSION_ORDER_PREFIX = "user_sessions_order:"
    # Default max concurrent sessions per user
    DEFAULT_MAX_SESSIONS = 5
    # Session TTL in seconds (24 hours)
    SESSION_TTL_SECONDS = 86400

    _lua_script_sha: str | None = None

    def __init__(self, redis_client: Any = _UNSET, max_sessions: int = DEFAULT_MAX_SESSIONS) -> None:
        """Initialize session manager.

        Args:
            redis_client: Async Redis client instance
            max_sessions: Maximum concurrent sessions per user
        """
        self._redis_client = redis_client
        self._max_sessions = max_sessions
        self._lua_script_registered: Any = None

    def set_redis_client(self, redis_client: Any) -> None:
        """Set Redis client for session tracking.

        Args:
            redis_client: Async Redis client instance
        """
        self._redis_client = redis_client
        logger.info("Session manager Redis client configured")

    @property
    def redis_client(self) -> Any:
        """Call-time acquisition of Redis client from provider if not explicitly injected."""
        if self._redis_client is not _UNSET:
            return self._redis_client
        from src.app.core.redis_provider import get_redis

        return get_redis()
    async def register_session(
        self,
        user_id: int | str,
        session_id: str,
        device_info: str | None = None,
    ) -> dict[str, Any]:
        """Register a new session for a user.

        If the user has reached the maximum sessions, the oldest sessions
        are automatically invalidated using optimized Redis pipeline.

        Performance Optimization (P2-1):
        - Uses ZRANGE (non-destructive read) instead of ZPOPMIN loop
        - Batches all HGET calls in a single pipeline
        - Batches all HDEL + ZREM in atomic pipeline
        - Reduces roundtrips from O(n) to O(1)

        Args:
            user_id: User identifier
            session_id: Unique session identifier (e.g., JWT jti)
            device_info: Optional device/browser info for display

        Returns
        -------
            Dict with session info and any invalidated sessions
        """
        client = self.redis_client
        if not client:
            logger.warning("Redis not configured for session management")
            return {"registered": False, "reason": "redis_unavailable"}
        hash_key = f"{self.SESSION_HASH_PREFIX}{user_id}"
        zset_key = f"{self.SESSION_ORDER_PREFIX}{user_id}"

        # Create timestamp for ordering (Unix timestamp as float for precision)
        now = datetime.now(UTC)
        timestamp = now.timestamp()

        # Create new session entry
        new_session = {
            "session_id": session_id,
            "device_info": device_info,
            "created_at": now.isoformat(),
            "last_accessed": now.isoformat(),
        }

        invalidated_sessions: list[dict[str, Any]] = []
        try:
            # N-7: atomic registration + eviction via the shared LuaScriptManager
            # (EVALSHA with automatic NOSCRIPT fallback owned centrally).
            result = cast(
                Any,
                await get_lua_manager().execute(
                    client,
                    "session_register_with_eviction",
                    keys=[zset_key, hash_key],
                    args=[
                        str(self._max_sessions),
                        session_id,
                        cast(str, json_dumps(new_session, as_str=True)),
                        str(timestamp),
                        str(self.SESSION_TTL_SECONDS),
                        str(settings.r1_unified.SESSION_MAX_EVICTION_BATCH_SIZE),
                    ],
                ),
            )

            if result and result[0] in (b"already_exists", "already_exists"):
                return {"registered": True, "already_exists": True}

            if result and result[0] in (b"success", "success"):
                final_count = int(result[1])
                if len(result) > 2:
                    # Sessions were evicted
                    for i in range(2, len(result)):
                        data = result[i]
                        if isinstance(data, bytes):
                            data = data.decode("utf-8")
                        invalidated_sessions.append(json_loads(data))
                    record_session_eviction(len(invalidated_sessions))  # Actual count of evicted sessions
            else:
                final_count = 0
        except Exception as e:
            logger.error("Session registration failed", user_id=user_id, error=str(e))
            return {"registered": False, "reason": "lua_error"}

        logger.info(
            "Session registered",
            user_id=user_id,
            session_id=session_id,
            active_sessions=final_count,
            max_sessions=self._max_sessions,
        )

        return {
            "registered": True,
            "active_sessions": final_count,
            "invalidated_sessions": invalidated_sessions,
        }

    async def is_session_valid(
        self,
        user_id: int | str,
        session_id: str,
        jwt_exp: float | None = None,
    ) -> bool:
        """Check if a session is valid (registered and not invalidated).

        When Redis is unavailable, a degraded mode is available: if ``jwt_exp``
        is provided and the token has not yet expired, the session is accepted.
        This avoids a total authentication outage during brief Redis downtime
        while bounding risk to the JWT expiry window.

        Args:
            user_id: User identifier
            session_id: Session identifier to check
            jwt_exp: Optional JWT ``exp`` claim (Unix timestamp) for degraded
                mode fallback when Redis is unavailable.  If *None*, the method
                falls back to fail-closed behaviour (returns False).

        Returns
        -------
            True if session is valid, False otherwise
        """
        client = self.redis_client
        # S-08 FIX: "unavailable" must mean BOTH "no client configured" AND
        # "the client raised". The guard below used to test `if not client`
        # only, but self.redis_client is a live Redis over a connection pool and
        # is never falsy during an outage - a ConnectionError/TimeoutError
        # propagated straight out of this method. The call chain
        # (core/principal.py:296 -> api/deps/auth.py _resolve_strict_bearer_flow
        # -> _enforce_session_valid -> here) has no try/except on the strict
        # path, so a Redis TIMEOUT - the common case, not a full outage - turned
        # every authenticated request into a 500 instead of the intended
        # 401/REVOKED_SESSION. On a Redis error we now fall through to the SAME
        # fail-closed/degraded policy the "no client" case uses, which is what
        # this docstring has always claimed.
        if client:
            hash_key = f"{self.SESSION_HASH_PREFIX}{user_id}"
            try:
                # O(1) lookup using Hash - HEXISTS (Hash is source of truth)
                return bool(await client.hexists(hash_key, session_id))
            except Exception as e:  # noqa: BLE001 - any Redis failure is "unavailable"
                logger.warning(
                    "session_validation_redis_error",
                    user_id=user_id,
                    session_id=session_id,
                    error=str(e),
                )
                # Fall through to the unavailable policy below rather than
                # propagating (500) or failing open.

        # --- Redis unavailable: no client configured, or the lookup raised. ---
        from src.app.core.config import settings
        from src.app.core.monitoring.r1_unified_metrics import record_fail_open

        # SECURITY FIX: Fail closed if configured for high-security environments
        if settings.auth.TOKEN_CACHE_FAILURE_MODE == "fail_closed":
            record_fail_open("session_validation_failed_closed")
            logger.critical(
                "redis_unavailable_session_rejected_fail_closed",
                extra={
                    "user_id": user_id,
                    "session_id": session_id,
                    "severity": "high",
                    "action": "session_validation_rejected_fail_closed",
                },
            )
            return False

        # Degraded mode: accept session if JWT is still within expiry
        if jwt_exp is not None:
            now = datetime.now(UTC).timestamp()
            if jwt_exp > now:
                record_fail_open("session_validation_degraded")
                logger.warning(
                    "redis_unavailable_session_accepted_degraded",
                    extra={
                        "user_id": user_id,
                        "session_id": session_id,
                        "jwt_exp": jwt_exp,
                        "remaining_seconds": round(jwt_exp - now),
                        "action": "degraded_mode_accept",
                    },
                )
                return True

        # Fail closed: no jwt_exp or token already expired
        record_fail_open("session_validation")

        logger.critical(
            "redis_unavailable_session_rejected",
            extra={
                "user_id": user_id,
                "session_id": session_id,
                "severity": "high",
                "action": "session_validation_rejected",
            },
        )
        return False

    async def invalidate_session(self, user_id: int | str, session_id: str) -> bool:
        """Invalidate a specific session (e.g., on logout).

        S-08: the Redis calls are guarded. A failure here means the session may
        or may not have been removed, so the honest answer is False - the caller
        must not be told the logout succeeded. Never a silent True.

        Args:
            user_id: User identifier
            session_id: Session identifier to invalidate

        Returns
        -------
            True if session was found and invalidated
        """
        client = self.redis_client
        if not client:
            return False
        hash_key = f"{self.SESSION_HASH_PREFIX}{user_id}"
        zset_key = f"{self.SESSION_ORDER_PREFIX}{user_id}"

        try:
            # Check if session exists before deleting
            exists = await client.hexists(hash_key, session_id)
            if not exists:
                return False

            # Delete from both Hash and ZSET atomically - O(1) + O(log n)
            async with client.pipeline() as pipe:
                pipe.hdel(hash_key, session_id)
                pipe.zrem(zset_key, session_id)
                await pipe.execute()
        except Exception as e:  # noqa: BLE001 - surface failure, never fake success
            logger.error("session_invalidation_failed", user_id=user_id, session_id=session_id, error=str(e))
            return False

        logger.info("Session invalidated", user_id=user_id, session_id=session_id)
        return True

    async def invalidate_all_sessions(self, user_id: int | str) -> int:
        """Invalidate all sessions for a user (e.g., password change).

        S-08: guarded, and it reports 0 rather than raising when Redis fails -
        the sessions are still live, so 0 is the truthful "nothing was
        invalidated" answer and the caller can surface it.

        Args:
            user_id: User identifier

        Returns
        -------
            Number of sessions invalidated
        """
        client = self.redis_client
        if not client:
            return 0
        hash_key = f"{self.SESSION_HASH_PREFIX}{user_id}"
        zset_key = f"{self.SESSION_ORDER_PREFIX}{user_id}"

        try:
            # Get count before deletion - O(1) with HLEN
            count: int = await client.hlen(hash_key)

            if count == 0:
                return 0

            # Delete both Hash and ZSET - O(1) with DEL
            async with client.pipeline() as pipe:
                pipe.delete(hash_key)
                pipe.delete(zset_key)
                await pipe.execute()
        except Exception as e:  # noqa: BLE001 - surface failure, never fake success
            logger.error("invalidate_all_sessions_failed", user_id=user_id, error=str(e))
            return 0

        logger.info(
            "All sessions invalidated",
            user_id=user_id,
            session_count=count,
        )
        return count

    async def get_active_sessions(self, user_id: int | str) -> list[dict]:
        """Get all active sessions for a user.

        Args:
            user_id: User identifier

        Returns
        -------
            List of active session info dicts
        """
        client = self.redis_client
        if not client:
            return []
        hash_key = f"{self.SESSION_HASH_PREFIX}{user_id}"

        # S-08: guarded - a read-path Redis failure degrades to an empty list
        # (the caller sees "no sessions" rather than a 500), and is logged.
        try:
            # Get all sessions from hash - O(N) but only when explicitly requested
            sessions_raw = await client.hgetall(hash_key)
        except Exception as e:  # noqa: BLE001 - read path degrades, never 500s a request
            logger.warning("get_active_sessions_redis_error", user_id=user_id, error=str(e))
            return []

        if not sessions_raw:
            return []

        # Parse all session data
        sessions = []
        for session_data in sessions_raw.values():
            sessions.append(json_loads(session_data))

        return sessions

    async def count_active_sessions(self, user_id: int | str) -> int:
        """Count active sessions for a user (optimized).

        Args:
            user_id: User identifier

        Returns
        -------
            Number of active sessions
        """
        client = self.redis_client
        if not client:
            return 0
        zset_key = f"{self.SESSION_ORDER_PREFIX}{user_id}"

        # S-08: guarded - a Redis failure reports 0 rather than raising.
        try:
            # O(1) count using ZCARD - prefer ZSET as it's the ordering source of truth
            count: int = await client.zcard(zset_key)

            # Fallback to HLEN if ZSET doesn't exist (migration case)
            if count == 0:
                hash_key = f"{self.SESSION_HASH_PREFIX}{user_id}"
                count = await client.hlen(hash_key)
        except Exception as e:  # noqa: BLE001 - read path degrades, never 500s a request
            logger.warning("count_active_sessions_redis_error", user_id=user_id, error=str(e))
            return 0

        return count

    async def migrate_sessions_to_zset(self, batch_size: int = 100) -> dict[str, Any]:
        """Migrate existing Hash sessions to include ZSET ordering.

        This populates the ZSET index from existing Hash data.
        Safe to run multiple times (idempotent - ZADD updates scores).

        Args:
            batch_size: Number of keys to process per batch

        Returns
        -------
            Dict with migration statistics
        """
        client = self.redis_client
        if not client:
            logger.warning("Redis not configured, skipping ZSET migration")
            return {"migrated": 0, "errors": []}

        logger.info("Starting session ZSET migration")
        migrated = 0
        errors: list[tuple[str, str]] = []

        # Scan for user_sessions_v2:* keys (Hash)
        match_pattern = f"{self.SESSION_HASH_PREFIX}*"

        cursor = 0
        while True:
            cursor, keys = await client.scan(cursor, match=match_pattern, count=batch_size)

            for key in keys:
                if isinstance(key, bytes):
                    key = key.decode("utf-8")

                try:
                    # Extract user_id from key
                    user_id = key.replace(self.SESSION_HASH_PREFIX, "")
                    zset_key = f"{self.SESSION_ORDER_PREFIX}{user_id}"

                    # Get all sessions from Hash
                    sessions_raw = await client.hgetall(key)
                    if not sessions_raw:
                        continue

                    # Build ZSET entries: session_id -> timestamp
                    zset_entries = {}
                    for session_id, session_data in sessions_raw.items():
                        if isinstance(session_id, bytes):
                            session_id = session_id.decode("utf-8")

                        session = json_loads(session_data)
                        created_at = session.get("created_at", "")

                        # Parse ISO timestamp to Unix timestamp
                        if created_at:
                            try:
                                # FIXED: Use more robust ISO parsing (Item 12)
                                dt = datetime.fromisoformat(created_at)
                                timestamp = dt.timestamp()
                            except ValueError:
                                timestamp = datetime.now(UTC).timestamp()
                        else:
                            timestamp = datetime.now(UTC).timestamp()

                        zset_entries[session_id] = timestamp

                    if zset_entries:
                        # Atomically add to ZSET with TTL
                        async with client.pipeline() as pipe:
                            pipe.zadd(zset_key, zset_entries)
                            pipe.expire(zset_key, self.SESSION_TTL_SECONDS)
                            await pipe.execute()

                        migrated += 1

                        if migrated % 100 == 0:
                            logger.info("Migrated users to ZSET", migrated=migrated)

                except Exception as e:
                    errors.append((key, str(e)))
                    logger.error("Failed to migrate to ZSET:", key=key, error=e)

            if cursor == 0:
                break

        logger.info("ZSET migration completed.", migrated=migrated, errors=len(errors))
        return {"migrated": migrated, "errors": errors}

    async def migrate_to_hash(self, batch_size: int = 100) -> int:
        """Migrate all legacy sessions to Redis Hash format.

        Iterates over all keys matching the legacy prefix and converts
        them to the new Hash format.

        Args:
            batch_size: Number of keys to process per batch (pipeline)

        Returns
        -------
            Number of users migrated
        """
        client = self.redis_client
        if not client:
            logger.warning("Redis not configured, skipping migration")
            return 0

        logger.info("Starting session migration to Hash format")
        count = 0

        # Scan for legacy keys
        # Pattern: user_sessions:*

        match_pattern = f"{self.SESSION_KEY_PREFIX}*"

        async for key in client.scan_iter(match=match_pattern):
            if isinstance(key, bytes):
                key = key.decode("utf-8")

            # Skip if it's already a v2 key (though prefix should prevent this if distinct)
            if key.startswith(self.SESSION_HASH_PREFIX):
                continue

            try:
                # Extract user_id from key
                # key is like "user_sessions:123"
                user_id = key.replace(self.SESSION_KEY_PREFIX, "")

                # Get legacy sessions
                sessions_raw = await client.get(key)
                if not sessions_raw:
                    continue

                sessions = json_loads(sessions_raw)
                if not sessions:
                    continue

                # Prepare Hash structure
                hash_key = f"{self.SESSION_HASH_PREFIX}{user_id}"

                # Use pipeline for atomicity per user
                async with client.pipeline(transaction=True) as pipe:
                    # Add all sessions to hash
                    for session in sessions:
                        pipe.hset(hash_key, session["session_id"], json_dumps(session, as_str=True))

                    # Set TTL (refresh it)
                    pipe.expire(hash_key, self.SESSION_TTL_SECONDS)

                    await pipe.execute()

                count += 1
                if count % 100 == 0:
                    logger.info("Migrated users", count=count)

            except Exception as e:
                logger.error("Failed to migrate session key :", key=key, error=e)

        logger.info("Session migration completed. Migrated users.", count=count)
        return count


# Global session manager instance
session_manager = SessionManager(max_sessions=settings.auth.AUTH_MAX_SESSIONS)
