"""SEC-08 at the call site: both identifier forms charge ONE account counter.

The alias split was a defect of ``login()``, not of the policy: "alice" and
"alice@example.com" resolved to the same account but counted separately, so an
attacker got 2x the budget and an operator saw only half the attempts.
Testing ``LoginLockoutPolicy.resolve_key`` alone only proves the policy
converges — only driving ``login()`` proves the call site uses the id.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ACCOUNT = {"id": 7, "username": "alice", "email": "alice@example.com", "is_active": True}


@pytest.fixture
def login_env():
    """login() wired to a counting Redis double, with the auth seams mocked."""
    from src.app.core.config import settings
    from src.app.modules.auth.service import AuthService
    from src.app.modules.auth.services.lockout import LoginLockoutPolicy

    counter: dict[str, int] = {}

    def _redis():
        redis = AsyncMock()

        async def _get(key):
            value = counter.get(key)
            return None if value is None else str(value).encode()

        async def _eval(_lua, _n, key, _threshold, _ttl):
            counter[key] = counter.get(key, 0) + 1
            return counter[key]

        async def _delete(key):
            return 1 if counter.pop(key, None) is not None else 0

        redis.get.side_effect = _get
        redis.eval.side_effect = _eval
        redis.delete.side_effect = _delete
        return redis

    service = AuthService.__new__(AuthService)
    service.lockout = LoginLockoutPolicy()

    async def _login(identifier: str, resolved=ACCOUNT) -> None:
        """One failed login. Raises UnauthorizedError, charging one counter."""
        with (
            patch.object(AuthService, "_resolve_user_auth_info", new_callable=AsyncMock, return_value=resolved),
            patch.object(AuthService, "authenticate_user", new_callable=AsyncMock, return_value=None),
            patch.object(AuthService, "_publish_login_audit", new_callable=AsyncMock),
        ):
            await service.login(
                identifier,
                "wrong-password",
                response=MagicMock(),
                request=MagicMock(),
                db=MagicMock(),
                redis=_redis(),
            )

    return _login, counter, settings.auth.AUTH_MAX_LOGIN_ATTEMPTS


class TestAliasFormsShareOneCounter:
    """The regression: one account, one budget, whichever form is used."""

    @pytest.mark.asyncio
    async def test_alternating_identifier_forms_charge_one_counter(self, login_env) -> None:
        """Username and email forms for ONE account accumulate together."""
        from src.app.shared.exceptions import UnauthorizedError

        login, counter, threshold = login_env

        for i in range(threshold + 1):
            identifier = "alice" if i % 2 == 0 else "alice@example.com"
            with pytest.raises(UnauthorizedError):
                await login(identifier)

        # Exactly one counter exists, and it is the ACCOUNT's. It saturates at
        # the threshold: once locked, the pre-auth check short-circuits before
        # charging, so the count cannot climb past the limit.
        assert list(counter) == ["login_attempts:user:7"]
        assert counter["login_attempts:user:7"] == threshold

    @pytest.mark.asyncio
    async def test_splitting_forms_does_not_double_the_budget(self, login_env) -> None:
        """Under the old identifier key, splitting the forms granted 2x.

        Each form had its own counter, so ``threshold`` attempts split across
        the two left both below the limit. Converged, the account locks at
        exactly ``threshold``.
        """
        from src.app.shared.exceptions import UnauthorizedError

        login, counter, threshold = login_env
        half = threshold // 2

        for _ in range(half):
            with pytest.raises(UnauthorizedError):
                await login("alice")
        assert counter["login_attempts:user:7"] == half

        for _ in range(threshold - half):
            with pytest.raises(UnauthorizedError):
                await login("alice@example.com")

        assert counter["login_attempts:user:7"] == threshold

    @pytest.mark.asyncio
    async def test_submitted_identifier_never_becomes_a_key(self, login_env) -> None:
        """The attacker-supplied string must not appear in any counter key."""
        from src.app.shared.exceptions import UnauthorizedError

        login, counter, _ = login_env

        with pytest.raises(UnauthorizedError):
            await login("alice")

        assert all("alice" not in key for key in counter)
        assert "login_attempts:user:7" in counter

    @pytest.mark.asyncio
    async def test_unresolved_account_charges_no_user_id(self, login_env) -> None:
        """A name that does not resolve must not fabricate an account key.

        The per-IP route budget (check_auth_rate_limit) is the control there;
        the lockout counter falls back to the namespaced unresolved key.
        """
        from src.app.shared.exceptions import UnauthorizedError

        login, counter, _ = login_env

        with pytest.raises(UnauthorizedError):
            await login("ghost", resolved=None)

        assert list(counter) == ["login_attempts:unresolved:ghost"]
        assert not any(key.startswith("login_attempts:user:") for key in counter)
