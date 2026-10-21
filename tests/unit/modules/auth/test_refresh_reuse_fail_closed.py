"""P2-3: refresh-reuse detection must not fail open, and must be alertable.

Every successful rotation plants ``refresh_rotated:{old_jti}``. Presenting a
token carrying that marker means the old token was stolen, so the family is
revoked: session killed, the attacker's replacement token blacklisted.

The defect: when the marker LOOKUP failed (Redis unavailable), the function
returned silently at ``logger.debug``. That downgraded theft detection from
"kill the family" to "reject this one token". The stolen token is still
refused — ``verify_token`` -> ``is_token_revoked`` has a DB fallback that fails
CLOSED — but the attacker's already-issued REPLACEMENT token survives until
its natural expiry.

The tell was internal: the same function logged at WARNING for a MALFORMED
marker, while a more dangerous condition (unavailable Redis) was quieter.

Scope note: making the marker survive a Redis outage needs a second
persistence path plus a migration, which the bead lists as a deliberate
decision rather than a fix. These tests pin what is decided here: the log
level, and that the presented token is still judged on revocation.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

MARKER = {
    "user_id": 7,
    "sid": "sess-1",
    "new_jti": "jti-new",
    "new_exp": 4102444800.0,
}


@pytest.fixture
def service():
    from src.app.modules.auth.service import AuthService

    return AuthService.__new__(AuthService)


def _token_with_jti(jti: str) -> str:
    """A structurally real JWT so extract_jti_unverified reads a real jti."""
    import jwt

    return jwt.encode({"sub": "u", "jti": jti, "type": "refresh"}, "k", algorithm="HS256")


class TestMarkerLookupFailureIsAlertable:
    """The degradation must be visible, not a debug line."""

    @pytest.mark.asyncio
    async def test_lookup_failure_logs_at_warning(self, service) -> None:

        cache = MagicMock()
        cache.get_strict = AsyncMock(side_effect=RuntimeError("redis down"))

        with (
            patch("src.app.core.cache.client.cache_client", cache),
            patch("src.app.modules.auth.service.logger") as mock_logger,
        ):
            await service._reject_reused_refresh_token(_token_with_jti("jti-old"))

        mock_logger.warning.assert_called()
        kwargs = mock_logger.warning.call_args.kwargs
        assert "reuse-marker" in mock_logger.warning.call_args.args[0]
        assert kwargs.get("error") == "redis down"

    @pytest.mark.asyncio
    async def test_lookup_failure_does_not_log_at_debug(self, service) -> None:
        """The exact defect: DEBUG for a more dangerous condition."""
        cache = MagicMock()
        cache.get_strict = AsyncMock(side_effect=RuntimeError("redis down"))

        with (
            patch("src.app.core.cache.client.cache_client", cache),
            patch("src.app.modules.auth.service.logger") as mock_logger,
        ):
            await service._reject_reused_refresh_token(_token_with_jti("jti-old"))

        mock_logger.debug.assert_not_called()


class TestReplayIsStillRejectedWithRedisDown:
    """The residual is bounded: the presented token is judged on revocation."""

    @pytest.mark.asyncio
    async def test_marker_lookup_failure_returns_without_raising(self, service) -> None:
        """This helper does not reject on lookup failure — verify_token does.

        Pinning the delegation: the family decision is lost, but the token
        reaches verify_token, whose is_token_revoked falls back to the DB and
        fails CLOSED.
        """
        cache = MagicMock()
        cache.get_strict = AsyncMock(side_effect=RuntimeError("redis down"))

        with (
            patch("src.app.core.cache.client.cache_client", cache),
            patch("src.app.modules.auth.service.logger"),
        ):
            # Returns None (no rejection here) rather than raising.
            await service._reject_reused_refresh_token(_token_with_jti("jti-old"))

    @pytest.mark.asyncio
    async def test_is_token_revoked_fails_closed_when_redis_is_down(self) -> None:
        """The oracle the flow relies on, exercised directly."""
        from src.app.core.security import jwt_service

        with (
            patch("src.app.core.security.jwt_service.is_token_blacklisted", new=AsyncMock(return_value=None)),
            patch("src.app.core.security.jwt_service._check_db_fallback", new=AsyncMock(return_value=None)),
        ):
            assert await jwt_service.is_token_revoked("tok", "jti", MagicMock()) is True

    @pytest.mark.asyncio
    async def test_is_token_revoked_fails_closed_on_db_error(self) -> None:
        from src.app.core.security import jwt_service

        with (
            patch("src.app.core.security.jwt_service.is_token_blacklisted", new=AsyncMock(return_value=None)),
            patch(
                "src.app.core.security.jwt_service._check_db_fallback",
                new=AsyncMock(side_effect=RuntimeError("db down")),
            ),
        ):
            # An error must read as revoked, not as "allowed".
            with pytest.raises(Exception):
                await jwt_service.is_token_revoked("tok", "jti", MagicMock())


class TestFamilyRevocationStillWorks:
    """REGRESSION LOCK: the Redis-available path is untouched."""

    @pytest.mark.asyncio
    async def test_marker_present_revokes_the_family(self, service) -> None:
        from src.app.shared.exceptions import UnauthorizedError

        cache = MagicMock()
        cache.get_strict = AsyncMock(return_value=json.dumps(MARKER).encode())

        with (
            patch("src.app.core.cache.client.cache_client", cache),
            patch(
                "src.app.core.security.session_manager.session_manager.invalidate_session",
                new=AsyncMock(return_value=True),
            ) as invalidate,
            patch(
                "src.app.core.security.token_blacklist_cache.add_token_to_blacklist",
                new=AsyncMock(return_value=True),
            ) as blacklist,
            patch("src.app.modules.auth.service.audit", new=AsyncMock()),
            patch("src.app.modules.auth.service.logger"),
        ):
            with pytest.raises(UnauthorizedError):
                await service._reject_reused_refresh_token(_token_with_jti("jti-old"))

        invalidate.assert_awaited_once_with(7, "sess-1")
        # The ATTACKER'S replacement token is what gets blacklisted.
        assert "jti-new" in str(blacklist.await_args)


class TestNoSpuriousWarningOnNormalFlow:
    """COUNTER-TEST: the fix must not fire on ordinary refreshes."""

    @pytest.mark.asyncio
    async def test_no_marker_means_no_warning(self, service) -> None:
        cache = MagicMock()
        cache.get_strict = AsyncMock(return_value=None)

        with (
            patch("src.app.core.cache.client.cache_client", cache),
            patch("src.app.modules.auth.service.logger") as mock_logger,
        ):
            await service._reject_reused_refresh_token(_token_with_jti("jti-fresh"))

        mock_logger.warning.assert_not_called()
        mock_logger.debug.assert_not_called()

    @pytest.mark.asyncio
    async def test_malformed_marker_still_takes_the_warning_path(self, service) -> None:
        """Unchanged: a corrupt value warns and returns."""
        cache = MagicMock()
        cache.get_strict = AsyncMock(return_value=b"not json")

        with (
            patch("src.app.core.cache.client.cache_client", cache),
            patch("src.app.modules.auth.service.logger") as mock_logger,
        ):
            await service._reject_reused_refresh_token(_token_with_jti("jti-old"))

        mock_logger.warning.assert_called()
        assert "malformed" in mock_logger.warning.call_args.args[0]

    @pytest.mark.asyncio
    async def test_unverifiable_jti_returns_quietly(self, service) -> None:
        cache = MagicMock()
        cache.get_strict = AsyncMock()

        with (
            patch("src.app.core.cache.client.cache_client", cache),
            patch("src.app.modules.auth.service.logger") as mock_logger,
        ):
            await service._reject_reused_refresh_token("not-a-jwt")

        mock_logger.warning.assert_not_called()
        cache.get_strict.assert_not_called()
