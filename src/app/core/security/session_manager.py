"""Session Manager for concurrent session limiting.

Prevents account sharing by limiting the number of concurrent sessions per user.
Tracks active sessions in Redis with automatic expiration.
"""

import json
from collections import deque
from datetime import UTC, datetime
from typing import Any

from structlog import get_logger

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

        session_key = f"{self.SESSION_KEY_PREFIX}{user_id}"

        # Get current sessions - use deque for O(1) popleft
        sessions_raw = await self._redis_client.get(session_key)
        sessions: deque[dict] = deque(json.loads(sessions_raw)) if sessions_raw else deque()

        # Check if session already exists
        existing_ids = [s["session_id"] for s in sessions]
        if session_id in existing_ids:
            return {"registered": True, "already_exists": True}

        # Create new session entry
        new_session = {
            "session_id": session_id,
            "device_info": device_info,
            "created_at": datetime.now(UTC).isoformat(),
        }

        invalidated_sessions = []

        # Remove oldest sessions if at limit - O(1) with deque.popleft()
        while len(sessions) >= self._max_sessions:
            oldest = sessions.popleft()  # O(1) removal from front
            invalidated_sessions.append(oldest)
            logger.info(
                "Session invalidated due to limit",
                user_id=user_id,
                invalidated_session=oldest["session_id"],
            )

        # Add new session
        sessions.append(new_session)

        # Save to Redis with TTL
        await self._redis_client.setex(
            session_key,
            self.SESSION_TTL_SECONDS,
            json.dumps(list(sessions)),
        )

        # Dual-write: Save to Redis Hash (v2)
        try:
            hash_key = f"{self.SESSION_HASH_PREFIX}{user_id}"
            # Check for limit on Hash (since we can't trust the deque state for the hash)
            # But during dual-write, we can just mirror the "new_session" addition
            # and rely on the deque logic to tell us what to remove?
            # Actually, `invalidated_sessions` tells us what was removed.

            # 1. Remove invalidated sessions from Hash
            if invalidated_sessions:
                await self._redis_client.hdel(hash_key, *[s["session_id"] for s in invalidated_sessions])

            # 2. Add new session to Hash
            await self._redis_client.hset(hash_key, session_id, json.dumps(new_session))

            # 3. Set Expiry on Hash
            await self._redis_client.expire(hash_key, self.SESSION_TTL_SECONDS)

        except Exception as e:
            logger.error(f"Failed to dual-write session to hash: {e}")

        logger.info(
            "Session registered",
            user_id=user_id,
            session_id=session_id,
            active_sessions=len(sessions),
            max_sessions=self._max_sessions,
        )

        return {
            "registered": True,
            "active_sessions": len(sessions),
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

        session_key = f"{self.SESSION_KEY_PREFIX}{user_id}"
        sessions_raw = await self._redis_client.get(session_key)

        if not sessions_raw:
            return False

        sessions = json.loads(sessions_raw)
        return any(s["session_id"] == session_id for s in sessions)

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

        session_key = f"{self.SESSION_KEY_PREFIX}{user_id}"
        sessions_raw = await self._redis_client.get(session_key)

        if not sessions_raw:
            return False

        sessions = json.loads(sessions_raw)
        original_count = len(sessions)
        sessions = [s for s in sessions if s["session_id"] != session_id]

        if len(sessions) == original_count:
            return False  # Session not found

        if sessions:
            await self._redis_client.setex(
                session_key,
                self.SESSION_TTL_SECONDS,
                json.dumps(sessions),
            )
        else:
            await self._redis_client.delete(session_key)

        # Dual-write: Remove from Hash (v2)
        try:
            hash_key = f"{self.SESSION_HASH_PREFIX}{user_id}"
            await self._redis_client.hdel(hash_key, session_id)
            # If empty, maybe delete key? Redis hashes automatically delete when empty,
            # but we might want to ensure it? hdel handles it.
        except Exception as e:
            logger.error(f"Failed to dual-write invalidation to hash: {e}")

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

        session_key = f"{self.SESSION_KEY_PREFIX}{user_id}"
        sessions_raw = await self._redis_client.get(session_key)

        if not sessions_raw:
            return 0

        sessions = json.loads(sessions_raw)
        count = len(sessions)
        await self._redis_client.delete(session_key)

        # Dual-write: Delete Hash (v2)
        try:
            hash_key = f"{self.SESSION_HASH_PREFIX}{user_id}"
            await self._redis_client.delete(hash_key)
        except Exception as e:
            logger.error(f"Failed to dual-write delete-all to hash: {e}")

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

        session_key = f"{self.SESSION_KEY_PREFIX}{user_id}"
        sessions_raw = await self._redis_client.get(session_key)

        if not sessions_raw:
            return []

        # Cast for mypy strictness
        from typing import cast

        return cast(list[dict[str, Any]], json.loads(sessions_raw))

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

        # Try Hash first (v2) if we are in read-from-hash mode (not yet)
        # For now, stick to v1 unless we want to test v2 reading

        # v1 implementation
        session_key = f"{self.SESSION_KEY_PREFIX}{user_id}"
        sessions_raw = await self._redis_client.get(session_key)
        if not sessions_raw:
            return 0
        return len(json.loads(sessions_raw))

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

                sessions = json.loads(sessions_raw)
                if not sessions:
                    continue

                # Prepare Hash structure
                hash_key = f"{self.SESSION_HASH_PREFIX}{user_id}"

                # Use pipeline for atomicity per user
                async with self._redis_client.pipeline(transaction=True) as pipe:
                    # Add all sessions to hash
                    for session in sessions:
                        pipe.hset(hash_key, session["session_id"], json.dumps(session))

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
