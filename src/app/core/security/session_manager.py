"""Session Manager for concurrent session limiting.

Prevents account sharing by limiting the number of concurrent sessions per user.
Tracks active sessions in Redis with automatic expiration.
"""

from datetime import UTC, datetime
from typing import Any

from structlog import get_logger

from src.app.core.json_utils import json_dumps, json_loads

logger = get_logger(__name__)


class SessionManager:
    """Manages user sessions for concurrent session limiting.

    Features:
    - Limits concurrent sessions per user (default: 5)
    - Tracks sessions in Redis with TTL
    - Automatically invalidates oldest sessions when limit exceeded
    """

    # Redis key prefix for user sessions
    SESSION_KEY_PREFIX = "user_sessions:"
    # Redis key prefix for user sessions (Hash implementation)
    SESSION_HASH_PREFIX = "user_sessions_v2:"
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

        If the user has reached the maximum sessions, the oldest session
        is automatically invalidated.

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

        # Check if session already exists - O(1) with HEXISTS
        exists = await self._redis_client.hexists(hash_key, session_id)
        if exists:
            return {"registered": True, "already_exists": True}

        # Create new session entry
        new_session = {
            "session_id": session_id,
            "device_info": device_info,
            "created_at": datetime.now(UTC).isoformat(),
        }

        invalidated_sessions = []

        # Get current session count - O(1) with HLEN
        current_count = await self._redis_client.hlen(hash_key)

        # If at limit, get all sessions and remove oldest
        if current_count >= self._max_sessions:
            # Get all sessions to find oldest
            all_sessions_raw = await self._redis_client.hgetall(hash_key)
            sessions_with_time = []
            for sid, sdata in all_sessions_raw.items():
                session_dict = json_loads(sdata)
                sessions_with_time.append((sid, session_dict, session_dict.get("created_at", "")))

            # Sort by created_at to find oldest
            sessions_with_time.sort(key=lambda x: x[2])

            # Remove oldest sessions until we're under the limit
            pipe = self._redis_client.pipeline()
            sessions_to_remove = current_count - self._max_sessions + 1

            for i in range(sessions_to_remove):
                if i < len(sessions_with_time):
                    sid, session_dict, _ = sessions_with_time[i]
                    pipe.hdel(hash_key, sid)
                    invalidated_sessions.append(session_dict)
                    logger.info(
                        "Session invalidated due to limit",
                        user_id=user_id,
                        invalidated_session=sid,
                    )

            # Add new session
            pipe.hset(hash_key, session_id, json_dumps(new_session, as_str=True))

            # Set expiry
            pipe.expire(hash_key, self.SESSION_TTL_SECONDS)

            await pipe.execute()
        else:
            # Under limit, just add new session
            pipe = self._redis_client.pipeline()
            pipe.hset(hash_key, session_id, json_dumps(new_session, as_str=True))
            pipe.expire(hash_key, self.SESSION_TTL_SECONDS)
            await pipe.execute()

        # Get final count
        final_count = await self._redis_client.hlen(hash_key)

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
            # Fail open if Redis unavailable - with critical logging for monitoring
            logger.critical(
                "redis_unavailable_session_bypass",
                extra={
                    "user_id": user_id,
                    "session_id": session_id,
                    "severity": "high",
                    "action": "session_validation_bypassed",
                },
            )
            return True

        # O(1) lookup using Hash - HEXISTS
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

        # Check if session exists before deleting
        exists = await self._redis_client.hexists(hash_key, session_id)
        if not exists:
            return False

        # Delete session from hash - O(1) with HDEL
        await self._redis_client.hdel(hash_key, session_id)

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

        # Get count before deletion - O(1) with HLEN
        count: int = await self._redis_client.hlen(hash_key)

        if count == 0:
            return 0

        # Delete entire hash - O(1) with DEL
        await self._redis_client.delete(hash_key)

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

        hash_key = f"{self.SESSION_HASH_PREFIX}{user_id}"

        # O(1) count using HLEN - no deserialization needed!
        count: int = await self._redis_client.hlen(hash_key)
        return count

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
