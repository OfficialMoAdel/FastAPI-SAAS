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

    # Atomic Lua script for login attempt checking.
    # Combines lockout check + increment + expire into single Redis roundtrip.
    # Returns -1 if locked out, otherwise the new attempt count.
    # OPT-11: Always execute EXPIRE to ensure constant-time Redis operations regardless of user existence.
    _LOGIN_CHECK_LUA = """
local attempts = redis.call('GET', KEYS[1])
if attempts and tonumber(attempts) >= tonumber(ARGV[1]) then
    return -1
end
local new_attempts = redis.call('INCR', KEYS[1])
-- SECURITY FIX: Always set/refresh expiry to ensure constant timing and prevent enumeration
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
return new_attempts
"""

    @staticmethod
    def attempts_key(username: str) -> str:
        """Redis key tracking failed login attempts for a normalized username."""
        return f"login_attempts:{username}"

    async def check_and_increment(self, redis: Redis, normalized_username: str) -> int:
        """Atomically check lockout and record a tentative attempt.

        Returns
        -------
        int
            The new attempt count, or ``-1`` if the account is locked out.

        Notes
        -----
        This records the attempt tentatively. If login succeeds, the caller
        must invoke :meth:`clear` to reset the counter.
        """
        result = await redis.eval(  # type: ignore[misc]
            self._LOGIN_CHECK_LUA,
            1,
            self.attempts_key(normalized_username),
            str(settings.AUTH_MAX_LOGIN_ATTEMPTS),
            str(settings.AUTH_LOCKOUT_DURATION_SECONDS),
        )

        # Coerce result to int to handle bytes when Redis client is not initialized
        # with decode_responses=True. Without this, b'-1' != -1, disabling lockout entirely.
        try:
            return int(result)
        except (TypeError, ValueError):
            return -1  # Fail-closed: default to locked on Redis error

    async def clear(self, redis: Redis, normalized_username: str) -> None:
        """Clear failed attempts after a successful login (single roundtrip)."""
        await redis.delete(self.attempts_key(normalized_username))
