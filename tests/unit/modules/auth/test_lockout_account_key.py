"""SEC-08: the login lockout counter is keyed on the RESOLVED account.

The old key was ``login_attempts:{submitted_identifier}``, which let an
unauthenticated caller lock any known or guessed account for the lockout
window, and split one account's budget in two because "alice" and
"alice@example.com" counted separately.

These tests pin the new contract: the account key is authoritative, both
identifier forms share it, and the unresolved path (spraying) still gets a
budget so brute-force guessing is not made cheaper.
"""

from unittest.mock import AsyncMock

import pytest


class TestKeyFormat:
    """The key itself — this IS the security property."""

    def test_account_key_uses_user_id(self) -> None:
        from src.app.modules.auth.services.lockout import LoginLockoutPolicy

        key = LoginLockoutPolicy.account_key(42)
        assert key == "login_attempts:user:42"
        assert "42" in key

    def test_unresolved_key_is_namespaced(self) -> None:
        """The submitted identifier never lands in an authoritative key."""
        from src.app.modules.auth.services.lockout import LoginLockoutPolicy

        key = LoginLockoutPolicy.attempts_key("victim@example.com")
        assert key == "login_attempts:unresolved:victim@example.com"
        # Must NOT collide with the account namespace an id would produce.
        assert key != LoginLockoutPolicy.account_key("victim@example.com")

    def test_resolve_key_prefers_the_account(self) -> None:
        from src.app.modules.auth.services.lockout import LoginLockoutPolicy

        assert LoginLockoutPolicy.resolve_key(None, 42, "alice") == "login_attempts:user:42"

    def test_resolve_key_falls_back_when_unresolved(self) -> None:
        from src.app.modules.auth.services.lockout import LoginLockoutPolicy

        key = LoginLockoutPolicy.resolve_key(None, None, "alice")
        assert key == "login_attempts:unresolved:alice"

    def test_two_identifier_forms_share_one_counter(self) -> None:
        """THE alias-split regression: same account id, one key."""
        from src.app.modules.auth.services.lockout import LoginLockoutPolicy

        by_username = LoginLockoutPolicy.resolve_key(None, 7, "alice")
        by_email = LoginLockoutPolicy.resolve_key(None, 7, "alice@example.com")
        assert by_username == by_email


class TestAccountLockout:
    """Attempts accumulate on the account, not the submitted string."""

    @pytest.mark.asyncio
    async def test_repeated_failures_lock_the_account(self) -> None:
        """Five wrong passwords on a real account lock THAT account."""
        from src.app.core.config import settings
        from src.app.modules.auth.services.lockout import LoginLockoutPolicy

        counter = {}
        redis = AsyncMock()

        async def _get(key):
            return str(counter.get(key, 0)).encode()

        async def _eval(_lua, _n, key, _threshold, _ttl):
            counter[key] = counter.get(key, 0) + 1
            return counter[key]

        redis.get.side_effect = _get
        redis.eval.side_effect = _eval

        lockout = LoginLockoutPolicy()
        for _ in range(settings.auth.AUTH_MAX_LOGIN_ATTEMPTS):
            await lockout.record_failed_attempt(redis, "alice", user_id=7)

        assert await lockout.is_locked(redis, "alice", user_id=7) is True
        assert counter == {"login_attempts:user:7": settings.auth.AUTH_MAX_LOGIN_ATTEMPTS}

    @pytest.mark.asyncio
    async def test_unknown_identifier_does_not_lock_a_real_account(self) -> None:
        """An attacker who does not know the id cannot lock the account.

        They can burn the unresolved budget for the string they submitted, but
        the account's own counter stays untouched.
        """
        from src.app.modules.auth.services.lockout import LoginLockoutPolicy

        counter = {}
        redis = AsyncMock()

        async def _get(key):
            return str(counter.get(key, 0)).encode()

        async def _eval(_lua, _n, key, _threshold, _ttl):
            counter[key] = counter.get(key, 0) + 1
            return counter[key]

        redis.get.side_effect = _get
        redis.eval.side_effect = _eval

        lockout = LoginLockoutPolicy()
        for _ in range(5):
            await lockout.record_failed_attempt(redis, "alice", user_id=None)

        # The account's counter was never charged.
        assert "login_attempts:user:7" not in counter
        assert await lockout.is_locked(redis, "alice", user_id=7) is False

    @pytest.mark.asyncio
    async def test_spray_across_unknown_names_still_budgeted(self) -> None:
        """Counter-test: brute-force guessing is not made cheaper.

        Each unresolved name has its own budget, and the per-IP route budget
        (auth.rate_limit) is the primary control there. This test pins that
        the per-identifier budget still exists.
        """
        from src.app.modules.auth.services.lockout import LoginLockoutPolicy

        counter = {}
        redis = AsyncMock()

        async def _get(key):
            return str(counter.get(key, 0)).encode()

        async def _eval(_lua, _n, key, _threshold, _ttl):
            counter[key] = counter.get(key, 0) + 1
            return counter[key]

        redis.get.side_effect = _get
        redis.eval.side_effect = _eval

        lockout = LoginLockoutPolicy()
        for _ in range(5):
            await lockout.record_failed_attempt(redis, "ghost-1", user_id=None)

        assert "login_attempts:unresolved:ghost-1" in counter
        assert await lockout.is_locked(redis, "ghost-1", user_id=None) is True

    @pytest.mark.asyncio
    async def test_redis_failure_fails_closed(self) -> None:
        """A Redis error locks rather than unlocks (unchanged contract)."""
        from src.app.modules.auth.services.lockout import LoginLockoutPolicy

        redis = AsyncMock()
        redis.get.side_effect = RuntimeError("redis down")

        lockout = LoginLockoutPolicy()
        assert await lockout.is_locked(redis, "alice", user_id=7) is True

    @pytest.mark.asyncio
    async def test_clear_resets_the_account_counter(self) -> None:
        from src.app.modules.auth.services.lockout import LoginLockoutPolicy

        redis = AsyncMock()
        lockout = LoginLockoutPolicy()
        await lockout.clear(redis, "alice", user_id=7)
        redis.delete.assert_awaited_once_with("login_attempts:user:7")
