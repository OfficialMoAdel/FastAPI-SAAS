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
from typing import Any

from structlog import get_logger

from src.app.core.config import settings
from src.app.core.json_utils import json_dumps, json_loads
from src.app.core.monitoring.r1_unified_metrics import record_session_eviction

logger = get_logger(__name__)


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

    def __init__(self, redis_client: Any = None, max_sessions: int = DEFAULT_MAX_SESSIONS) -> None:
        """Initialize session manager.

        Args:
            redis_client: Async Redis client instance
            max_sessions: Maximum concurrent sessions per user
        """
        self._redis_client = redis_client
        self._max_sessions = max_sessions

    def set_redis_client(self, redis_client: Any) -> None:
        """Set Redis client for session tracking.

        Args:
            redis_client: Async Redis client instance
        """
        self._redis_client = redis_client
        logger.info("Session manager Redis client configured")

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
        if not self._redis_client:
            logger.warning("Redis not configured for session management")
            return {"registered": False, "reason": "redis_unavailable"}

        hash_key = f"{self.SESSION_HASH_PREFIX}{user_id}"
        zset_key = f"{self.SESSION_ORDER_PREFIX}{user_id}"

        # Check if session already exists - O(1) with HEXISTS
        exists = await self._redis_client.hexists(hash_key, session_id)
        if exists:
            return {"registered": True, "already_exists": True}

        # Create timestamp for ordering (Unix timestamp as float for precision)
        now = datetime.now(UTC)
        timestamp = now.timestamp()

        # Create new session entry
        new_session = {
            "session_id": session_id,
            "device_info": device_info,
            "created_at": now.isoformat(),
        }

        invalidated_sessions: list[dict[str, Any]] = []

        # Get current session count - O(1) with ZCARD (use ZSET as source of truth for count)
        current_count = await self._redis_client.zcard(zset_key)

        # Fallback to HLEN if ZSET doesn't exist yet (migration case)
        if current_count == 0:
            current_count = await self._redis_client.hlen(hash_key)

        # If at limit, evict oldest sessions using optimized pipeline
        if current_count >= self._max_sessions:
            sessions_to_remove = current_count - self._max_sessions + 1

            # Cap batch size to prevent memory exhaustion
            max_batch = settings.SESSION_MAX_EVICTION_BATCH_SIZE
            sessions_to_remove = min(sessions_to_remove, max_batch)

            if settings.SESSION_USE_PIPELINE_EVICTION:
                # Optimized path: Use ZRANGE + Pipeline (P2-1 optimization)
                roundtrips = await self._evict_sessions_pipelined(
                    hash_key=hash_key,
                    zset_key=zset_key,
                    sessions_to_remove=sessions_to_remove,
                    new_session_id=session_id,
                    new_session_data=new_session,
                    timestamp=timestamp,
                    invalidated_sessions=invalidated_sessions,
                    user_id=user_id,
                )
                record_session_eviction(roundtrips)
            else:
                # Original path: ZPOPMIN loop (kept for fallback)
                roundtrips = await self._evict_sessions_legacy(
                    hash_key=hash_key,
                    zset_key=zset_key,
                    sessions_to_remove=sessions_to_remove,
                    new_session_id=session_id,
                    new_session_data=new_session,
                    timestamp=timestamp,
                    invalidated_sessions=invalidated_sessions,
                    user_id=user_id,
                )
                record_session_eviction(roundtrips)
        else:
            # Under limit, just add new session to both structures
            pipe = self._redis_client.pipeline()
            pipe.zadd(zset_key, {session_id: timestamp})
            pipe.hset(hash_key, session_id, json_dumps(new_session, as_str=True))
            pipe.expire(zset_key, self.SESSION_TTL_SECONDS)
            pipe.expire(hash_key, self.SESSION_TTL_SECONDS)
            await pipe.execute()

        # Get final count from ZSET
        final_count = await self._redis_client.zcard(zset_key)

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

    async def _evict_sessions_pipelined(
        self,
        hash_key: str,
        zset_key: str,
        sessions_to_remove: int,
        new_session_id: str,
        new_session_data: dict[str, Any],
        timestamp: float,
        invalidated_sessions: list[dict[str, Any]],
        user_id: int | str,
    ) -> int:
        """Evict oldest sessions using optimized Redis pipeline.

        Uses ZRANGE (non-destructive) + pipeline for batch operations.
        Reduces roundtrips from O(n) to O(1).

        Args:
            hash_key: Redis hash key for session data
            zset_key: Redis sorted set key for ordering
            sessions_to_remove: Number of sessions to evict
            new_session_id: ID of new session to add
            new_session_data: Data for new session
            timestamp: Timestamp for new session ordering
            invalidated_sessions: List to append invalidated session data
            user_id: User ID for logging

        Returns:
            Number of Redis roundtrips used (for metrics)
        """
        roundtrips = 0

        # Step 1: READ oldest sessions (non-destructive) - 1 roundtrip
        oldest_sessions = await self._redis_client.zrange(zset_key, 0, sessions_to_remove - 1, withscores=True)
        roundtrips += 1

        if not oldest_sessions:
            # No sessions to evict, just add new session
            pipe = self._redis_client.pipeline()
            pipe.zadd(zset_key, {new_session_id: timestamp})
            pipe.hset(hash_key, new_session_id, json_dumps(new_session_data, as_str=True))
            pipe.expire(zset_key, self.SESSION_TTL_SECONDS)
            pipe.expire(hash_key, self.SESSION_TTL_SECONDS)
            await pipe.execute()
            roundtrips += 1
            return roundtrips

        # Decode session IDs
        session_ids = [s[0].decode("utf-8") if isinstance(s[0], bytes) else s[0] for s in oldest_sessions]

        # Step 2: Batch HGET for session data - 1 roundtrip
        pipe = self._redis_client.pipeline()
        for sid in session_ids:
            pipe.hget(hash_key, sid)
        session_data_list = await pipe.execute()
        roundtrips += 1

        # Step 3: Collect invalidated sessions (with error handling)
        for sid, data in zip(session_ids, session_data_list):
            if data is None:
                logger.warning(
                    "Session in sorted set but not in hash (orphaned)",
                    user_id=user_id,
                    session_id=sid,
                )
                continue

            try:
                invalidated_sessions.append(json_loads(data))
            except Exception as e:
                logger.error(
                    "Failed to parse session data",
                    user_id=user_id,
                    session_id=sid,
                    error=str(e),
                )

        # Step 4: Atomic delete from BOTH hash and sorted set + add new - 1 roundtrip
        pipe = self._redis_client.pipeline()
        for sid in session_ids:
            pipe.hdel(hash_key, sid)
            pipe.zrem(zset_key, sid)  # Critical: remove from sorted set too!

            logger.info(
                "Session invalidated due to limit",
                user_id=user_id,
                invalidated_session=sid,
            )

        # Add new session
        pipe.zadd(zset_key, {new_session_id: timestamp})
        pipe.hset(hash_key, new_session_id, json_dumps(new_session_data, as_str=True))

        # Set expiry on both keys
        pipe.expire(zset_key, self.SESSION_TTL_SECONDS)
        pipe.expire(hash_key, self.SESSION_TTL_SECONDS)

        await pipe.execute()
        roundtrips += 1

        return roundtrips

    async def _evict_sessions_legacy(
        self,
        hash_key: str,
        zset_key: str,
        sessions_to_remove: int,
        new_session_id: str,
        new_session_data: dict[str, Any],
        timestamp: float,
        invalidated_sessions: list[dict[str, Any]],
        user_id: int | str,
    ) -> int:
        """Evict oldest sessions using legacy ZPOPMIN loop.

        Original implementation kept for fallback and comparison.

        Returns:
            Number of Redis roundtrips used (for metrics)
        """
        roundtrips = 0

        pipe = self._redis_client.pipeline()

        for _ in range(sessions_to_remove):
            # ZPOPMIN returns [(member, score)] - O(log n)
            oldest = await self._redis_client.zpopmin(zset_key, count=1)
            roundtrips += 1

            if oldest:
                oldest_session_id = oldest[0][0]
                if isinstance(oldest_session_id, bytes):
                    oldest_session_id = oldest_session_id.decode("utf-8")

                # Get session data before deletion for return value
                session_data = await self._redis_client.hget(hash_key, oldest_session_id)
                roundtrips += 1

                if session_data:
                    invalidated_sessions.append(json_loads(session_data))

                # Delete from hash - O(1)
                pipe.hdel(hash_key, oldest_session_id)

                logger.info(
                    "Session invalidated due to limit",
                    user_id=user_id,
                    invalidated_session=oldest_session_id,
                )

        # Add new session to both ZSET and Hash atomically
        pipe.zadd(zset_key, {new_session_id: timestamp})
        pipe.hset(hash_key, new_session_id, json_dumps(new_session_data, as_str=True))

        # Set expiry on both keys
        pipe.expire(zset_key, self.SESSION_TTL_SECONDS)
        pipe.expire(hash_key, self.SESSION_TTL_SECONDS)

        await pipe.execute()
        roundtrips += 1

        return roundtrips

    async def is_session_valid(self, user_id: int | str, session_id: str) -> bool:
        """Check if a session is valid (registered and not invalidated).

        Args:
            user_id: User identifier
            session_id: Session identifier to check

        Returns
        -------
            True if session is valid, False otherwise
        """
        if not self._redis_client:
            # SECURITY: Fail closed if Redis unavailable - reject when session state cannot be verified
            # This prevents invalidated sessions from being accepted during Redis outages
            from src.app.core.monitoring.r1_unified_metrics import record_fail_open

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

        # O(1) lookup using Hash - HEXISTS (Hash is source of truth for existence)
        hash_key = f"{self.SESSION_HASH_PREFIX}{user_id}"
        exists: bool = await self._redis_client.hexists(hash_key, session_id)
        return exists

    async def invalidate_session(self, user_id: int | str, session_id: str) -> bool:
        """Invalidate a specific session (e.g., on logout).

        Args:
            user_id: User identifier
            session_id: Session identifier to invalidate

        Returns
        -------
            True if session was found and invalidated
        """
        if not self._redis_client:
            return False

        hash_key = f"{self.SESSION_HASH_PREFIX}{user_id}"
        zset_key = f"{self.SESSION_ORDER_PREFIX}{user_id}"

        # Check if session exists before deleting
        exists = await self._redis_client.hexists(hash_key, session_id)
        if not exists:
            return False

        # Delete from both Hash and ZSET atomically - O(1) + O(log n)
        pipe = self._redis_client.pipeline()
        pipe.hdel(hash_key, session_id)
        pipe.zrem(zset_key, session_id)
        await pipe.execute()

        logger.info("Session invalidated", user_id=user_id, session_id=session_id)
        return True

    async def invalidate_all_sessions(self, user_id: int | str) -> int:
        """Invalidate all sessions for a user (e.g., password change).

        Args:
            user_id: User identifier

        Returns
        -------
            Number of sessions invalidated
        """
        if not self._redis_client:
            return 0

        hash_key = f"{self.SESSION_HASH_PREFIX}{user_id}"
        zset_key = f"{self.SESSION_ORDER_PREFIX}{user_id}"

        # Get count before deletion - O(1) with HLEN
        count: int = await self._redis_client.hlen(hash_key)

        if count == 0:
            return 0

        # Delete both Hash and ZSET - O(1) with DEL
        pipe = self._redis_client.pipeline()
        pipe.delete(hash_key)
        pipe.delete(zset_key)
        await pipe.execute()

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
        if not self._redis_client:
            return []

        hash_key = f"{self.SESSION_HASH_PREFIX}{user_id}"

        # Get all sessions from hash - O(N) but only when explicitly requested
        sessions_raw = await self._redis_client.hgetall(hash_key)

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
        if not self._redis_client:
            return 0

        zset_key = f"{self.SESSION_ORDER_PREFIX}{user_id}"

        # O(1) count using ZCARD - prefer ZSET as it's the ordering source of truth
        count: int = await self._redis_client.zcard(zset_key)

        # Fallback to HLEN if ZSET doesn't exist (migration case)
        if count == 0:
            hash_key = f"{self.SESSION_HASH_PREFIX}{user_id}"
            count = await self._redis_client.hlen(hash_key)

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
        if not self._redis_client:
            logger.warning("Redis not configured, skipping ZSET migration")
            return {"migrated": 0, "errors": []}

        logger.info("Starting session ZSET migration")
        migrated = 0
        errors: list[tuple[str, str]] = []

        # Scan for user_sessions_v2:* keys (Hash)
        match_pattern = f"{self.SESSION_HASH_PREFIX}*"

        cursor = 0
        while True:
            cursor, keys = await self._redis_client.scan(cursor, match=match_pattern, count=batch_size)

            for key in keys:
                if isinstance(key, bytes):
                    key = key.decode("utf-8")

                try:
                    # Extract user_id from key
                    user_id = key.replace(self.SESSION_HASH_PREFIX, "")
                    zset_key = f"{self.SESSION_ORDER_PREFIX}{user_id}"

                    # Get all sessions from Hash
                    sessions_raw = await self._redis_client.hgetall(key)
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
                                dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                                timestamp = dt.timestamp()
                            except ValueError:
                                timestamp = datetime.now(UTC).timestamp()
                        else:
                            timestamp = datetime.now(UTC).timestamp()

                        zset_entries[session_id] = timestamp

                    if zset_entries:
                        # Atomically add to ZSET with TTL
                        pipe = self._redis_client.pipeline()
                        pipe.zadd(zset_key, zset_entries)
                        pipe.expire(zset_key, self.SESSION_TTL_SECONDS)
                        await pipe.execute()

                        migrated += 1

                        if migrated % 100 == 0:
                            logger.info(f"Migrated {migrated} users to ZSET")

                except Exception as e:
                    errors.append((key, str(e)))
                    logger.error(f"Failed to migrate {key} to ZSET: {e}")

            if cursor == 0:
                break

        logger.info(f"ZSET migration completed. Migrated {migrated} users, {len(errors)} errors.")
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
        if not self._redis_client:
            logger.warning("Redis not configured, skipping migration")
            return 0

        logger.info("Starting session migration to Hash format")
        count = 0

        # Scan for legacy keys
        # Pattern: user_sessions:*

        match_pattern = f"{self.SESSION_KEY_PREFIX}*"

        async for key in self._redis_client.scan_iter(match=match_pattern):
            # Skip if it's already a v2 key (though prefix should prevent this if distinct)
            if key.startswith(self.SESSION_HASH_PREFIX):
                continue

            try:
                # Extract user_id from key
                # key is like "user_sessions:123"
                user_id = key.replace(self.SESSION_KEY_PREFIX, "")

                # Get legacy sessions
                sessions_raw = await self._redis_client.get(key)
                if not sessions_raw:
                    continue

                sessions = json_loads(sessions_raw)
                if not sessions:
                    continue

                # Prepare Hash structure
                hash_key = f"{self.SESSION_HASH_PREFIX}{user_id}"

                # Use pipeline for atomicity per user
                async with self._redis_client.pipeline(transaction=True) as pipe:
                    # Add all sessions to hash
                    for session in sessions:
                        pipe.hset(hash_key, session["session_id"], json_dumps(session, as_str=True))

                    # Set TTL (refresh it)
                    pipe.expire(hash_key, self.SESSION_TTL_SECONDS)

                    await pipe.execute()

                count += 1
                if count % 100 == 0:
                    logger.info(f"Migrated {count} users")

            except Exception as e:
                logger.error(f"Failed to migrate session key {key}: {e}")

        logger.info(f"Session migration completed. Migrated {count} users.")
        return count


# Global session manager instance
session_manager = SessionManager()
