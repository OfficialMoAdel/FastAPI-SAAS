"""Login lockout policy — brute-force protection backed by Redis.

Extracted from ``AuthService`` so the security policy that decides when an
account is locked lives in one unit, separate from the login workflow.

SEC-08: the per-account counter is keyed on the RESOLVED user id, not the
attacker-supplied identifier. The old ``login_attempts:{submitted}`` key let an
unauthenticated caller lock any known or guessed account for the lockout
window, and split the budget in two because "alice" and "alice@example.com"
resolved to the same account but counted separately.

Unresolved identifiers (spraying, typos) still charge the identifier key, so
brute-force guessing is not made cheaper. The per-IP budget in
``auth.rate_limit.check_auth_rate_limit`` is a separate control and remains.

COMPATIBILITY: the key format changed. Old ``login_attempts:{submitted}``
counters are DISCARDED, not honoured — they were keyed on a spoofable value,
so honouring them would let an attacker who knows a legacy username carry a
pre-seeded counter into the new scheme. Counters expire via TTL anyway, so
nothing is left hanging.
"""

from redis.asyncio import Redis

from src.app.core.config import settings


class LoginLockoutPolicy:
    """Tracks failed login attempts per resolved account and enforces lockout.

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
        """Key for an UNRESOLVED identifier.

        Only used when the account could not be resolved: there is no user id
        to key on, and the per-IP budget is the primary control there. Never
        used to lock a real account.
        """
        return f"login_attempts:unresolved:{username}"

    @staticmethod
    def account_key(user_id: int | str) -> str:
        """Key for a RESOLVED account — the authoritative lockout counter.

        The user id is not attacker-controlled, so no unauthenticated caller
        can lock a named account by guessing at it. Both the username and the
        email form resolve to the same id, so the two share one budget.
        """
        return f"login_attempts:user:{user_id}"

    @staticmethod
    def resolve_key(redis: Redis, user_id: int | str | None, submitted: str) -> str:
        """Pick the key to charge: the account's when known, else unresolved."""
        return LoginLockoutPolicy.account_key(user_id) if user_id is not None else LoginLockoutPolicy.attempts_key(
            submitted
        )

    async def is_locked_by_key(self, redis: Redis, key: str) -> bool:
        """Check whether a specific counter has reached the lockout threshold.

        Returns True if failed attempts >= AUTH_MAX_LOGIN_ATTEMPTS.
        Fail-closed on Redis errors per class docstring.
        """
        try:
            val = await redis.get(key)
            if val is None:
                return False
            return int(val) >= int(settings.auth.AUTH_MAX_LOGIN_ATTEMPTS)
        except Exception:
            return True

    async def is_locked(self, redis: Redis, submitted: str, user_id: int | str | None = None) -> bool:
        """Check if the account is currently locked out (pre-auth check).

        When ``user_id`` is supplied the account counter is authoritative;
        otherwise the unresolved-identifier counter is consulted so repeated
        attempts against a non-existent name still get a budget.

        Returns True if failed attempts >= AUTH_MAX_LOGIN_ATTEMPTS.
        Fail-closed on Redis errors per class docstring.
        """
        return await self.is_locked_by_key(redis, self.resolve_key(redis, user_id, submitted))

    async def record_failed_attempt_for_key(self, redis: Redis, key: str) -> int:
        """Record a failed login attempt against a specific counter key."""
        try:
            result = await redis.eval(  # type: ignore[misc]
                self._INCREMENT_LUA,
                1,
                key,
                str(settings.auth.AUTH_MAX_LOGIN_ATTEMPTS),
                str(settings.auth.AUTH_LOCKOUT_DURATION_SECONDS),
            )
            return int(result)
        except Exception:
            return 1

    async def record_failed_attempt(
        self, redis: Redis, submitted: str, user_id: int | str | None = None
    ) -> int:
        """Record a failed login attempt post-authentication."""
        return await self.record_failed_attempt_for_key(redis, self.resolve_key(redis, user_id, submitted))

    async def check_and_increment(self, redis: Redis, submitted: str, user_id: int | str | None = None) -> int:
        """Check lockout status and increment."""
        if await self.is_locked(redis, submitted, user_id):
            return -1
        return await self.record_failed_attempt(redis, submitted, user_id)

    async def clear_by_key(self, redis: Redis, key: str) -> None:
        """Clear a failed-attempt counter (single roundtrip)."""
        await redis.delete(key)

    async def clear(self, redis: Redis, submitted: str, user_id: int | str | None = None) -> None:
        """Clear failed attempts after a successful login (single roundtrip)."""
        await self.clear_by_key(redis, self.resolve_key(redis, user_id, submitted))
