"""Session rotation at the TokenIssuer seam.

TokenIssuer.rotate_session owns everything after verification: distributed
lock → rotated pair (sid/absolute_exp/iat carried, never extended) →
old-token blacklist → rotation marker → session invalidate + re-register →
single cookie write → auth.login_issued audit (channel="refresh").
AuthService keeps only the verification pre-checks; the router no longer
writes the refresh cookie.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Response


def _fake_jwt(payload: dict[str, Any]) -> str:
    """Unsigned JWT-shaped string — sufficient for unverified claim extraction."""
    import base64
    import json

    def b64(obj: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    return f"{b64({'alg': 'none'})}.{b64(payload)}.sig"


def _mock_user() -> dict[str, Any]:
    return {
        "id": 42,
        "username": "alice",
        "email": "alice@example.com",
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "token_version": 0,
        "tier_id": 1,
    }


def _old_payload() -> dict[str, Any]:
    return {
        "jti": "old-jti-1",
        "sid": "sid-1",
        "absolute_exp": 9_999_999_999.0,
        "iat": 1_700_000_000.0,
        "exp": 9_999_999_000.0,
        "sub": "alice",
    }


class _FakeCache:
    """Minimal cache_client double: real client present, in-memory store."""

    def __init__(self) -> None:
        self.client: Any = object()
        self.store: dict[str, Any] = {}
        self.nx_calls: list[str] = []
        self.deleted: list[str] = []

    async def set_nx(self, key: str, value: str, ttl: int) -> bool:
        self.nx_calls.append(key)
        if key in self.store:
            return False
        self.store[key] = value
        return True

    async def set(self, key: str, value: str, ttl: int | None = None) -> None:
        self.store[key] = value

    async def delete(self, *keys: str) -> int:
        for key in keys:
            self.store.pop(key, None)
            self.deleted.append(key)
        return len(keys)


def _mock_request() -> MagicMock:
    request = MagicMock()
    request.headers = {"user-agent": "TestAgent/1.0"}
    request.client = MagicMock()
    request.client.host = "203.0.113.10"
    return request


class TestRotateSession:
    @pytest.mark.asyncio
    async def test_full_choreography_once_each(self):
        """Lock → rotate → blacklist → marker → invalidate → register →
        one cookie → one audit event."""
        from src.app.modules.auth.services.tokens import TokenIssuer

        cache = _FakeCache()
        request = _mock_request()
        response = Response()
        db = AsyncMock()
        new_refresh = _fake_jwt({"jti": "new-jti-1", "exp": 9_999_999_000.0})

        with (
            patch.object(
                TokenIssuer, "rotate_token_pair", new_callable=AsyncMock,
                return_value=("new-access", new_refresh),
            ) as mock_rotate,
            patch("src.app.core.cache.client.cache_client", cache),
            patch("src.app.core.security.blacklist_token", new_callable=AsyncMock) as mock_blacklist,
            patch("src.app.core.security.session_manager.session_manager") as mock_sessions,
            patch(
                "src.app.modules.auth.services.tokens.register_login_session", new_callable=AsyncMock
            ) as mock_register,
            patch("src.app.core.utils.ip_utils.get_client_ip", return_value="203.0.113.10"),
        ):
            mock_sessions.invalidate_session = AsyncMock()
            with patch("src.app.core.events.event_bus") as mock_bus:
                mock_bus.publish = AsyncMock()
                issued = await TokenIssuer.rotate_session(
                    token_data={"sub": "alice"},
                    old_refresh_payload=_old_payload(),
                    old_refresh_token="old-refresh-token",
                    user=_mock_user(),
                    db=db,
                    request=request,
                    response=response,
                    device_label="TestAgent/1.0",
                )

        assert issued.access_token == "new-access"
        assert issued.refresh_token == new_refresh
        # Lock acquired and released.
        assert cache.nx_calls == ["refresh_lock:old-jti-1"]
        assert "refresh_lock:old-jti-1" in cache.deleted
        # Rotation + blacklist + marker.
        mock_rotate.assert_awaited_once()
        mock_blacklist.assert_awaited_once()
        assert mock_blacklist.call_args.kwargs["token"] == "old-refresh-token"
        assert "refresh_rotated:old-jti-1" in cache.store
        # Session invalidated under carried sid, re-registered under same sid.
        mock_sessions.invalidate_session.assert_awaited_once_with(42, "sid-1")
        mock_register.assert_awaited_once()
        assert mock_register.call_args.kwargs["session_id"] == "sid-1"
        assert mock_register.call_args.kwargs["verify"] is False
        # Exactly one cookie write carrying the new refresh token.
        set_cookies = response.headers.getlist("set-cookie")
        refresh_cookies = [c for c in set_cookies if new_refresh in c]
        assert len(set_cookies) == len(refresh_cookies) == 1
        # Exactly one audit event, refresh channel.
        mock_bus.publish.assert_awaited_once()
        args, _ = mock_bus.publish.call_args
        assert args[0] == "auth.login_issued"
        assert args[1]["channel"] == "refresh"
        assert args[1]["user_id"] == "42"

    @pytest.mark.asyncio
    async def test_lock_contention_rejects_without_rotating(self):
        """A held lock means another rotation is in flight — reject, mint nothing."""
        from src.app.modules.auth.services.tokens import TokenIssuer
        from src.app.shared.exceptions import UnauthorizedError

        cache = _FakeCache()
        cache.store["refresh_lock:old-jti-1"] = "1"  # lock already held

        with (
            patch.object(
                TokenIssuer, "rotate_token_pair", new_callable=AsyncMock,
                return_value=("new-access", "new-refresh"),
            ) as mock_rotate,
            patch("src.app.core.cache.client.cache_client", cache),
        ):
            with pytest.raises(UnauthorizedError):
                await TokenIssuer.rotate_session(
                    token_data={"sub": "alice"},
                    old_refresh_payload=_old_payload(),
                    old_refresh_token="old-refresh-token",
                    user=_mock_user(),
                    db=AsyncMock(),
                )
        mock_rotate.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_carries_session_claims_into_new_pair(self):
        """sid/absolute_exp/iat from the old payload reach the minted tokens."""
        from src.app.modules.auth.services.tokens import TokenIssuer

        seen: dict[str, Any] = {}

        async def fake_create_access(data: dict[str, Any], expires_delta: Any = None) -> str:
            seen.update(data)
            return "new-access"

        async def fake_create_refresh(data: dict[str, Any]) -> str:
            return "new-refresh"

        cache = _FakeCache()
        with (
            patch("src.app.modules.auth.services.tokens.create_access_token", side_effect=fake_create_access),
            patch("src.app.modules.auth.services.tokens.create_refresh_token", side_effect=fake_create_refresh),
            patch("src.app.core.cache.client.cache_client", cache),
            patch("src.app.core.security.blacklist_token", new_callable=AsyncMock),
            patch("src.app.core.security.session_manager.session_manager") as mock_sessions,
            patch("src.app.modules.auth.services.tokens.register_login_session", new_callable=AsyncMock),
        ):
            mock_sessions.invalidate_session = AsyncMock()
            with patch("src.app.core.events.event_bus") as mock_bus:
                mock_bus.publish = AsyncMock()
                await TokenIssuer.rotate_session(
                    token_data={"sub": "alice"},
                    old_refresh_payload=_old_payload(),
                    old_refresh_token="old-refresh-token",
                    user=_mock_user(),
                    db=AsyncMock(),
                )
        assert seen["sid"] == "sid-1"
        assert seen["absolute_exp"] == 9_999_999_999.0
        assert seen["iat"] == 1_700_000_000.0

    @pytest.mark.asyncio
    async def test_no_response_no_cookie_but_session_registered(self):
        """Without a response there is nothing to write to — registration still happens."""
        from src.app.modules.auth.services.tokens import TokenIssuer

        cache = _FakeCache()
        with (
            patch.object(
                TokenIssuer, "rotate_token_pair", new_callable=AsyncMock,
                return_value=("new-access", "new-refresh"),
            ),
            patch("src.app.core.cache.client.cache_client", cache),
            patch("src.app.core.security.blacklist_token", new_callable=AsyncMock),
            patch("src.app.core.security.session_manager.session_manager") as mock_sessions,
            patch(
                "src.app.modules.auth.services.tokens.register_login_session", new_callable=AsyncMock
            ) as mock_register,
        ):
            mock_sessions.invalidate_session = AsyncMock()
            with patch("src.app.core.events.event_bus") as mock_bus:
                mock_bus.publish = AsyncMock()
                issued = await TokenIssuer.rotate_session(
                    token_data={"sub": "alice"},
                    old_refresh_payload=_old_payload(),
                    old_refresh_token="old-refresh-token",
                    user=_mock_user(),
                    db=AsyncMock(),
                )
        assert issued.refresh_token == "new-refresh"
        mock_register.assert_awaited_once()
        mock_bus.publish.assert_awaited_once()
