"""Session Manager for concurrent session limiting.

Prevents account sharing by limiting the number of concurrent sessions per user.
Tracks active sessions in Redis with automatic expiration.
"""

import json
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

        # Get current sessions
        sessions_raw = await self._redis_client.get(session_key)
        sessions: list[dict] = json.loads(sessions_raw) if sessions_raw else []

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

        # Remove oldest sessions if at limit
        while len(sessions) >= self._max_sessions:
            oldest = sessions.pop(0)  # Remove oldest (FIFO)
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
            json.dumps(sessions),
        )

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

        return json.loads(sessions_raw)


# Global session manager instance
session_manager = SessionManager()
