"""Phase 2.2: one auth-info snapshot per login.

``login`` resolves the account's auth payload BEFORE the lockout decision,
because the lockout counter is keyed on the resolved user id (SEC-08). It then
called ``authenticate_user``, which resolved the SAME identifier again — the
identical row, read twice.

``authenticate_user`` now accepts the already-resolved snapshot, so the
password check and the lockout decision read ONE row version. These tests pin
that, plus the behaviours the snapshot must not disturb: the dummy-bcrypt
timing path, the SSO-only sentinel, and the transparent rehash.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from src.app.core.config import settings
from src.app.modules.auth.service import AuthService
from src.app.shared.exceptions import UnauthorizedError

TENANT_ID = UUID("11111111-1111-4111-8111-111111111111")


def _stored_hash() -> str:
    """A bcrypt-shaped hash at the CONFIGURED cost, so no rehash is triggered."""
    return f"$2b${int(settings.crypt.BCRYPT_ROUNDS):02d}$" + "x" * 53


def _snapshot(**overrides: object) -> dict:
    """The auth payload shape ``get_user_with_tier_eager`` / the cache holds."""
    payload: dict = {
        "id": 7,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": None,
        "name": "Test User",
        "username": "testuser",
        "email": "test@example.com",
        "profile_image_url": None,
        "tenant_id": str(TENANT_ID),
        "tier_id": 1,
        "hashed_password": _stored_hash(),
        "is_superuser": False,
        "is_deleted": False,
        "is_active": True,
        "is_email_verified": True,
        "token_version": 0,
        "tier": {"id": 1, "created_at": "2026-01-01T00:00:00Z", "updated_at": None, "name": "Free"},
    }
    payload.update(overrides)
    return payload


class TestAuthenticateUserAcceptsASnapshot:
    """``authenticate_user`` uses the caller's snapshot instead of re-reading."""

    @pytest.mark.asyncio
    async def test_a_supplied_snapshot_skips_the_second_resolution(self) -> None:
        service = AuthService()
        snapshot = _snapshot()

        with (
            patch.object(AuthService, "_resolve_user_auth_info", new_callable=AsyncMock) as resolve,
            patch(
                "src.app.modules.auth.service.verify_password", new_callable=AsyncMock, return_value=True
            ) as verify,
        ):
            result = await service.authenticate_user("testuser", "secret", AsyncMock(), auth_info=snapshot)

        assert result is snapshot
        resolve.assert_not_awaited()
        verify.assert_awaited_once_with("secret", snapshot["hashed_password"])

    @pytest.mark.asyncio
    async def test_without_a_snapshot_the_existing_behaviour_is_unchanged(self) -> None:
        """Every other caller still resolves for itself (no API break)."""
        service = AuthService()
        db = AsyncMock()
        snapshot = _snapshot()

        with (
            patch.object(
                AuthService, "_resolve_user_auth_info", new_callable=AsyncMock, return_value=snapshot
            ) as resolve,
            patch("src.app.modules.auth.service.verify_password", new_callable=AsyncMock, return_value=True),
        ):
            result = await service.authenticate_user("testuser", "secret", db)

        resolve.assert_awaited_once_with("testuser", db)
        assert result is snapshot

    @pytest.mark.asyncio
    async def test_a_wrong_password_still_returns_none(self) -> None:
        service = AuthService()

        with (
            patch.object(AuthService, "_resolve_user_auth_info", new_callable=AsyncMock),
            patch("src.app.modules.auth.service.verify_password", new_callable=AsyncMock, return_value=False),
        ):
            assert await service.authenticate_user("testuser", "wrong", AsyncMock(), auth_info=_snapshot()) is None

    @pytest.mark.asyncio
    async def test_an_unknown_account_still_pays_a_dummy_bcrypt(self) -> None:
        """The snapshot being absent must not open a timing oracle."""
        service = AuthService()

        with (
            patch.object(AuthService, "_resolve_user_auth_info", new_callable=AsyncMock, return_value=None),
            patch(
                "src.app.modules.auth.service.verify_password", new_callable=AsyncMock, return_value=False
            ) as verify,
        ):
            result = await service.authenticate_user("ghost", "secret", AsyncMock())

        assert result is None
        assert verify.await_args.args[1] == AuthService._dummy_bcrypt_hash()

    @pytest.mark.asyncio
    async def test_the_sso_only_sentinel_never_reaches_bcrypt(self) -> None:
        """An SSO-only row fails cleanly instead of raising UnknownHashError."""
        service = AuthService()
        snapshot = _snapshot(hashed_password="!SSO_ONLY_ACCOUNT_NO_PASSWORD")

        with (
            patch.object(AuthService, "_resolve_user_auth_info", new_callable=AsyncMock),
            patch(
                "src.app.modules.auth.service.verify_password", new_callable=AsyncMock, return_value=True
            ) as verify,
        ):
            result = await service.authenticate_user("testuser", "secret", AsyncMock(), auth_info=snapshot)

        assert result is None
        assert verify.await_args.args[1] != "!SSO_ONLY_ACCOUNT_NO_PASSWORD"

    @pytest.mark.asyncio
    async def test_a_verified_snapshot_password_still_rehashes(self) -> None:
        """The A7 transparent upgrade is not lost by skipping the re-read."""
        service = AuthService()
        snapshot = _snapshot()

        with (
            patch.object(AuthService, "_resolve_user_auth_info", new_callable=AsyncMock),
            patch("src.app.modules.auth.service.verify_password", new_callable=AsyncMock, return_value=True),
            patch("src.app.modules.auth.service.needs_rehash", return_value=True),
            patch("src.app.modules.auth.service.get_password_hash", new_callable=AsyncMock, return_value="new"),
            patch("src.app.modules.auth.service.UserService") as user_service,
        ):
            user_service.return_value.update_password_hash = AsyncMock()
            result = await service.authenticate_user("testuser", "secret", AsyncMock(), auth_info=snapshot)

        assert result is snapshot
        user_service.return_value.update_password_hash.assert_awaited_once_with(snapshot["id"], "new")


def _redis(attempts: bytes | None = None) -> AsyncMock:
    """A Redis client whose lockout probe reports ``attempts`` (None = no counter)."""
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=attempts)
    redis.delete = AsyncMock()
    redis.eval = AsyncMock(return_value=1)
    return redis


def _mfa_service() -> MagicMock:
    """An MFA service that never challenges (login continues to token issuance)."""
    mfa = MagicMock()
    mfa.challenge_if_required = AsyncMock(return_value=None)
    return mfa


class TestLoginResolvesTheSnapshotOnce:
    """The login workflow reads the account payload exactly once."""

    @pytest.mark.asyncio
    async def test_a_successful_login_resolves_the_directory_once(self) -> None:
        service = AuthService()
        snapshot = _snapshot()

        with (
            patch.object(
                AuthService, "_resolve_user_auth_info", new_callable=AsyncMock, return_value=snapshot
            ) as resolve,
            patch("src.app.modules.auth.service.verify_password", new_callable=AsyncMock, return_value=True),
            patch(
                "src.app.modules.auth.services.tokens.create_access_token",
                new_callable=AsyncMock,
                return_value="access",
            ),
            patch(
                "src.app.modules.auth.services.tokens.create_refresh_token",
                new_callable=AsyncMock,
                return_value="refresh",
            ),
            patch("src.app.modules.auth.services.tokens.register_login_session", new_callable=AsyncMock),
            patch("src.app.modules.auth.services.mfa.get_mfa_service", return_value=_mfa_service()),
        ):
            result = await service.login("testuser", "secret", MagicMock(), None, AsyncMock(), _redis())

        assert result["access_token"] == "access"
        assert resolve.await_count == 1, "login must not resolve the same identifier twice"

    @pytest.mark.asyncio
    async def test_a_locked_account_never_reaches_the_password_check(self) -> None:
        """The lockout refusal happens on the SAME snapshot, before bcrypt."""
        service = AuthService()
        locked = str(int(settings.auth.AUTH_MAX_LOGIN_ATTEMPTS)).encode()

        with (
            patch.object(
                AuthService, "_resolve_user_auth_info", new_callable=AsyncMock, return_value=_snapshot()
            ) as resolve,
            patch.object(AuthService, "authenticate_user", new_callable=AsyncMock) as authenticate,
            patch("src.app.modules.auth.service.audit", new_callable=AsyncMock),
        ):
            with pytest.raises(UnauthorizedError):
                await service.login("testuser", "secret", MagicMock(), None, AsyncMock(), _redis(locked))

        assert resolve.await_count == 1
        authenticate.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_wrong_password_charges_the_resolved_account(self) -> None:
        """The failure path keeps keying the counter on the resolved user id."""
        service = AuthService()
        redis = _redis()

        with (
            patch.object(
                AuthService, "_resolve_user_auth_info", new_callable=AsyncMock, return_value=_snapshot()
            ),
            patch("src.app.modules.auth.service.verify_password", new_callable=AsyncMock, return_value=False),
            patch("src.app.modules.auth.service.audit", new_callable=AsyncMock),
        ):
            with pytest.raises(UnauthorizedError):
                await service.login("testuser", "wrong", MagicMock(), None, AsyncMock(), redis)

        assert redis.eval.await_args.args[2] == f"login_attempts:user:{_snapshot()['id']}"

    @pytest.mark.asyncio
    async def test_an_unknown_account_still_401s_and_charges_the_identifier(self) -> None:
        service = AuthService()
        redis = _redis()

        with (
            patch.object(AuthService, "_resolve_user_auth_info", new_callable=AsyncMock, return_value=None),
            patch("src.app.modules.auth.service.verify_password", new_callable=AsyncMock, return_value=False),
            patch("src.app.modules.auth.service.audit", new_callable=AsyncMock),
        ):
            with pytest.raises(UnauthorizedError):
                await service.login("ghost", "secret", MagicMock(), None, AsyncMock(), redis)

        assert redis.eval.await_args.args[2] == "login_attempts:unresolved:ghost"

    @pytest.mark.asyncio
    async def test_the_snapshot_is_the_one_handed_to_the_password_check(self) -> None:
        """The handover itself: login's resolved dict reaches authenticate_user."""
        service = AuthService()
        snapshot = _snapshot(id=42)
        db = AsyncMock()

        with (
            patch.object(
                AuthService, "_resolve_user_auth_info", new_callable=AsyncMock, return_value=snapshot
            ) as resolve,
            patch.object(AuthService, "authenticate_user", new_callable=AsyncMock, return_value=None) as authenticate,
            patch("src.app.modules.auth.service.audit", new_callable=AsyncMock),
        ):
            with pytest.raises(UnauthorizedError):
                await service.login("testuser", "secret", MagicMock(), None, db, _redis())

        assert resolve.await_count == 1
        assert authenticate.await_args.kwargs["auth_info"] is snapshot
        assert authenticate.await_args.kwargs["db"] is db


class TestTheSnapshotIsNotAPrivilegeEscalation:
    """The snapshot is data, not authorisation: the liveness gates still run."""

    @pytest.mark.asyncio
    async def test_an_inactive_snapshot_is_rejected_after_a_valid_password(self) -> None:
        service = AuthService()

        with (
            patch.object(
                AuthService,
                "_resolve_user_auth_info",
                new_callable=AsyncMock,
                return_value=_snapshot(is_active=False),
            ),
            patch("src.app.modules.auth.service.verify_password", new_callable=AsyncMock, return_value=True),
            patch("src.app.modules.auth.service.audit", new_callable=AsyncMock),
        ):
            with pytest.raises(UnauthorizedError):
                await service.login("testuser", "secret", MagicMock(), None, AsyncMock(), _redis())

    @pytest.mark.asyncio
    async def test_a_snapshot_without_a_tier_still_authenticates(self) -> None:
        """``tier: None`` is legal (users without a tier row) and must survive."""
        service = AuthService()
        snapshot = _snapshot(tier=None, tier_id=None, tenant_id=uuid4())

        with (
            patch.object(
                AuthService, "_resolve_user_auth_info", new_callable=AsyncMock, return_value=snapshot
            ),
            patch(
                "src.app.modules.auth.service.verify_password", new_callable=AsyncMock, return_value=True
            ) as verify,
        ):
            result = await service.authenticate_user("testuser", "secret", AsyncMock(), auth_info=snapshot)

        assert result is snapshot
        assert verify.await_args.args[1] == snapshot["hashed_password"]
