"""Behavioral tests for S-4: SESSION_REGISTRATION_FAIL_MODE in register_login_session.

Covers both failure signals from SessionManager.register_session:
1. A raised ``redis.exceptions.RedisError`` (connection failure mid-call).
2. A ``{"registered": False}`` result (Redis unconfigured / Lua error) — the
   path that previously slipped past the fail-mode check entirely.

Default mode is fail_closed (503); fail_open lets the login proceed.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import redis

from src.app.core.security.login_helpers import register_login_session
from src.app.shared.exceptions import ServiceUnavailableError


def _patch_settings(mode: str) -> MagicMock:
    mock_settings = MagicMock()
    mock_settings.auth.SESSION_REGISTRATION_FAIL_MODE = mode
    return mock_settings


class TestRegisterLoginSessionFailMode:
    @pytest.mark.asyncio
    async def test_redis_error_fail_closed_raises_503(self) -> None:
        """Raised RedisError + default fail_closed -> HTTP 503.

        A6: the refusal is a DOMAIN error (ServiceUnavailableError, mapped
        to 503 by the central handler), not ``fastapi.HTTPException`` — an
        HTTP response object must not escape below the transport layer into
        ARQ/worker contexts. The wire status is unchanged.
        """
        with (
            patch(
                "src.app.core.security.login_helpers.global_session_manager"
            ) as mock_sm,
            patch(
                "src.app.core.security.login_helpers.settings",
                _patch_settings("fail_closed"),
            ),
        ):
            mock_sm.register_session = AsyncMock(
                side_effect=redis.exceptions.RedisError("connection lost")
            )
            with pytest.raises(ServiceUnavailableError) as exc_info:
                await register_login_session(
                    user_id=1, access_token="tok", request=None, verify=False
                )
            assert exc_info.value.http_status == 503

    @pytest.mark.asyncio
    async def test_redis_error_fail_open_proceeds(self) -> None:
        """Raised RedisError + fail_open -> login proceeds (no exception)."""
        with (
            patch(
                "src.app.core.security.login_helpers.global_session_manager"
            ) as mock_sm,
            patch(
                "src.app.core.security.login_helpers.settings",
                _patch_settings("fail_open"),
            ),
        ):
            mock_sm.register_session = AsyncMock(
                side_effect=redis.exceptions.RedisError("connection lost")
            )
            await register_login_session(
                user_id=1, access_token="tok", request=None, verify=False
            )  # must not raise

    @pytest.mark.asyncio
    async def test_registered_false_fail_closed_raises_503(self) -> None:
        """{"registered": False} result + fail_closed -> HTTP 503.

        Regression for the advisory: register_session returns this shape
        WITHOUT raising when Redis is unconfigured or the Lua script errors;
        the helper must still honor the fail mode.
        """
        with (
            patch(
                "src.app.core.security.login_helpers.global_session_manager"
            ) as mock_sm,
            patch(
                "src.app.core.security.login_helpers.settings",
                _patch_settings("fail_closed"),
            ),
        ):
            mock_sm.register_session = AsyncMock(
                return_value={"registered": False, "reason": "redis_unavailable"}
            )
            with pytest.raises(ServiceUnavailableError) as exc_info:
                await register_login_session(
                    user_id=1, access_token="tok", request=None, verify=False
                )
            assert exc_info.value.http_status == 503

    @pytest.mark.asyncio
    async def test_registered_false_fail_open_proceeds(self) -> None:
        """{"registered": False} result + fail_open -> login proceeds."""
        with (
            patch(
                "src.app.core.security.login_helpers.global_session_manager"
            ) as mock_sm,
            patch(
                "src.app.core.security.login_helpers.settings",
                _patch_settings("fail_open"),
            ),
        ):
            mock_sm.register_session = AsyncMock(
                return_value={"registered": False, "reason": "lua_error"}
            )
            await register_login_session(
                user_id=1, access_token="tok", request=None, verify=False
            )  # must not raise

    @pytest.mark.asyncio
    async def test_registered_true_never_raises(self) -> None:
        """Successful registration never triggers the fail mode."""
        with (
            patch(
                "src.app.core.security.login_helpers.global_session_manager"
            ) as mock_sm,
            patch(
                "src.app.core.security.login_helpers.settings",
                _patch_settings("fail_closed"),
            ),
        ):
            mock_sm.register_session = AsyncMock(
                return_value={"registered": True, "active_sessions": 1}
            )
            await register_login_session(
                user_id=1, access_token="tok", request=None, verify=False
            )  # must not raise
            mock_sm.register_session.assert_awaited_once()
