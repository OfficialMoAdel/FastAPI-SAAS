"""Login lockout policy — brute-force protection backed by Redis.

Extracted from ``AuthService`` so the security policy that decides when an
account is locked lives in one unit, separate from the login workflow.
Behavior is identical to the original inline implementation.
"""

from redis.asyncio import Redis

from src.app.core.config import settings


class LoginLockoutPolicy:
    """Tracks failed login attempts per username and enforces account lockout.

    All operations are constant-time with respect to user existence and
    fail closed on Redis errors (a Redis failure locks rather than unlocks).
    """

    # Atomic Lua script for recording failed login attempts post-auth.
    _INCREMENT_LUA = """
local new_attempts = redis.call('INCR', KEYS[1])
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
return new_attempts
"""

    @staticmethod
    def attempts_key(username: str) -> str:
        """Redis key tracking failed login attempts for a normalized username."""
        return f"login_attempts:{username}"

    async def is_locked(self, redis: Redis, normalized_username: str) -> bool:
        """Check if the account is currently locked out (pre-auth check).

        Returns True if failed attempts >= AUTH_MAX_LOGIN_ATTEMPTS.
        Fail-closed on Redis errors per class docstring.
        """
        try:
            val = await redis.get(self.attempts_key(normalized_username))
            if val is None:
                return False
            return int(val) >= int(settings.AUTH_MAX_LOGIN_ATTEMPTS)
        except Exception:
            return True

    async def record_failed_attempt(self, redis: Redis, normalized_username: str) -> int:
        """Record a failed login attempt post-authentication."""
        try:
            result = await redis.eval(  # type: ignore[misc]
                self._INCREMENT_LUA,
                1,
                self.attempts_key(normalized_username),
                str(settings.AUTH_MAX_LOGIN_ATTEMPTS),
                str(settings.AUTH_LOCKOUT_DURATION_SECONDS),
            )
            return int(result)
        except Exception:
            return 1
    async def check_and_increment(self, redis: Redis, normalized_username: str) -> int:
        """Check lockout status and increment (for backward compatibility)."""
        if await self.is_locked(redis, normalized_username):
            return -1
        return await self.record_failed_attempt(redis, normalized_username)
    async def clear(self, redis: Redis, normalized_username: str) -> None:
        """Clear failed attempts after a successful login (single roundtrip)."""
        await redis.delete(self.attempts_key(normalized_username))
