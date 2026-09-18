"""P2-#8: login audit at the TokenIssuer issuance seam.

auth.login_issued is published INSIDE TokenIssuer.issue_session so every
login door (password, sso, passkey, magic_link, mfa) is audited exactly
once. The audit_logs subscription (handle_login_issued_event) translates
the event into an audit row with the door's historical action name
(auth.sso_login for SSO, auth.login for every other channel).
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _mock_user() -> dict[str, Any]:
    return {
        "id": 42,
        "username": "alice",
        "email": "alice@example.com",
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "token_version": 0,
        "tier_id": 1,
        "tier_name": "free",
    }


def _mock_request() -> MagicMock:
    request = MagicMock()
    request.headers = {"user-agent": "TestAgent/1.0"}
    request.client = MagicMock()
    request.client.host = "203.0.113.10"
    return request


class TestIssueSessionPublishesLoginIssued:
    """TokenIssuer.issue_session must publish auth.login_issued exactly once."""

    @pytest.mark.asyncio
    async def test_publishes_event_with_door_context(self):
        from src.app.modules.auth.services.tokens import TokenIssuer

        user = _mock_user()
        request = _mock_request()

        with (
            patch("src.app.modules.auth.services.tokens.create_access_token", new_callable=AsyncMock) as mock_at,
            patch("src.app.modules.auth.services.tokens.create_refresh_token", new_callable=AsyncMock) as mock_rt,
            patch("src.app.modules.auth.services.tokens.register_login_session", new_callable=AsyncMock),
            patch("src.app.core.utils.ip_utils.get_client_ip", return_value="203.0.113.10"),
        ):
            mock_at.return_value = "access-token"
            mock_rt.return_value = "refresh-token"

            with patch.object(
                TokenIssuer, "build_token_data", staticmethod(lambda **kwargs: {"sid": "sid-1"})
            ):
                with patch("src.app.core.events.event_bus") as mock_bus:
                    mock_bus.publish = AsyncMock()
                    issued = await TokenIssuer.issue_session(user, channel="password", request=request)
        assert issued.access_token == "access-token"
        assert issued.refresh_token == "refresh-token"
        mock_bus.publish.assert_awaited_once()
        args, kwargs = mock_bus.publish.call_args
        event_name = args[0]
        payload = args[1]
        assert event_name == "auth.login_issued"
        assert payload["user_id"] == "42"
        assert payload["tenant_id"] == "11111111-1111-1111-1111-111111111111"
        assert payload["channel"] == "password"
        assert payload["username"] == "alice"
        assert payload["email"] == "alice@example.com"
        assert payload["ip_address"] == "203.0.113.10"
        assert payload["user_agent"] == "TestAgent/1.0"

    @pytest.mark.asyncio
    async def test_audit_details_rides_payload(self):
        """Door-specific context (e.g. SSO provider) merges into the payload."""
        from src.app.modules.auth.services.tokens import TokenIssuer

        user = _mock_user()

        with (
            patch("src.app.modules.auth.services.tokens.create_access_token", new_callable=AsyncMock) as mock_at,
            patch("src.app.modules.auth.services.tokens.create_refresh_token", new_callable=AsyncMock) as mock_rt,
            patch("src.app.modules.auth.services.tokens.register_login_session", new_callable=AsyncMock),
        ):
            mock_at.return_value = "access-token"
            mock_rt.return_value = "refresh-token"

            with patch.object(
                TokenIssuer, "build_token_data", staticmethod(lambda **kwargs: {"sid": "sid-1"})
            ):
                with patch("src.app.core.events.event_bus") as mock_bus:
                    mock_bus.publish = AsyncMock()
                    await TokenIssuer.issue_session(
                        user,
                        channel="sso",
                        audit_details={"provider": "google"},
                    )

        payload = mock_bus.publish.call_args[0][1]
        assert payload["channel"] == "sso"
        assert payload["provider"] == "google"

    @pytest.mark.asyncio
    async def test_publish_failure_never_breaks_issuance(self):
        """Audit publish errors are swallowed — tokens still returned."""
        from src.app.modules.auth.services.tokens import TokenIssuer

        user = _mock_user()

        with (
            patch("src.app.modules.auth.services.tokens.create_access_token", new_callable=AsyncMock) as mock_at,
            patch("src.app.modules.auth.services.tokens.create_refresh_token", new_callable=AsyncMock) as mock_rt,
            patch("src.app.modules.auth.services.tokens.register_login_session", new_callable=AsyncMock),
        ):
            mock_at.return_value = "access-token"
            mock_rt.return_value = "refresh-token"

            with patch.object(
                TokenIssuer, "build_token_data", staticmethod(lambda **kwargs: {"sid": "sid-1"})
            ):
                with patch("src.app.core.events.event_bus") as mock_bus:
                    mock_bus.publish = AsyncMock(side_effect=RuntimeError("bus down"))
                    issued = await TokenIssuer.issue_session(user, channel="passkey")

        assert issued.access_token == "access-token"
        assert issued.refresh_token == "refresh-token"


class TestHandleLoginIssuedEvent:
    """The audit_logs subscription translates auth.login_issued into rows."""

    @pytest.mark.asyncio
    async def test_password_channel_writes_auth_login_row(self):
        from src.app.modules.audit_logs.service import handle_login_issued_event

        with patch("src.app.modules.audit_logs.service.buffer_audit_log", new_callable=AsyncMock) as mock_buf:
            await handle_login_issued_event(
                {
                    "user_id": "42",
                    "tenant_id": "11111111-1111-1111-1111-111111111111",
                    "channel": "password",
                    "username": "alice",
                    "email": "alice@example.com",
                    "ip_address": "203.0.113.10",
                    "user_agent": "TestAgent/1.0",
                }
            )

        mock_buf.assert_awaited_once()
        log_data = mock_buf.call_args[0][0]
        assert log_data["action"] == "auth.login"
        assert log_data["entity_type"] == "user"
        assert log_data["user_id"] == "42"
        assert log_data["details"]["success"] is True
        assert log_data["details"]["channel"] == "password"
        assert log_data["ip_address"] == "203.0.113.10"

    @pytest.mark.asyncio
    async def test_sso_channel_writes_auth_sso_login_with_provider(self):
        from src.app.modules.audit_logs.service import handle_login_issued_event

        with patch("src.app.modules.audit_logs.service.buffer_audit_log", new_callable=AsyncMock) as mock_buf:
            await handle_login_issued_event(
                {
                    "user_id": "42",
                    "tenant_id": "11111111-1111-1111-1111-111111111111",
                    "channel": "sso",
                    "username": "alice",
                    "email": "alice@example.com",
                    "provider": "google",
                }
            )

        log_data = mock_buf.call_args[0][0]
        assert log_data["action"] == "auth.sso_login"
        assert log_data["details"]["provider"] == "google"
        assert log_data["details"]["success"] is True

    @pytest.mark.asyncio
    async def test_missing_tenant_falls_back_to_system(self):
        from src.app.modules.audit_logs.service import handle_login_issued_event

        with patch("src.app.modules.audit_logs.service.buffer_audit_log", new_callable=AsyncMock) as mock_buf:
            await handle_login_issued_event(
                {"user_id": "42", "channel": "magic_link", "username": "alice"}
            )

        log_data = mock_buf.call_args[0][0]
        assert log_data["tenant_id"] == "00000000-0000-0000-0000-000000000000"

    @pytest.mark.asyncio
    async def test_invalid_payload_is_dropped_not_raised(self):
        from src.app.modules.audit_logs.service import handle_login_issued_event

        # No channel/user_id — handler must return quietly.
        await handle_login_issued_event({})

    @pytest.mark.asyncio
    async def test_writer_failure_is_best_effort_without_second_fallback(self) -> None:
        from src.app.modules.audit_logs.service import handle_login_issued_event

        with (
            patch(
                "src.app.modules.audit_logs.service.buffer_audit_log",
                new_callable=AsyncMock,
                side_effect=RuntimeError("writer failed"),
            ) as writer,
            patch(
                "src.app.modules.audit_logs.service._direct_insert_log", new_callable=AsyncMock
            ) as mock_insert,
            patch("src.app.core.config.settings.auth.AUDIT_LOG_FAILURE_MODE", "fail_closed"),
        ):
            await handle_login_issued_event(
                {"user_id": "42", "channel": "mfa", "username": "alice"}
            )

        writer.assert_awaited_once()
        mock_insert.assert_not_awaited()
        assert writer.await_args is not None
        assert writer.await_args.args[0]["action"] == "auth.login"


class TestDoorRequestForwarding:
    """Passkey + magic-link doors thread the HTTP request to the seam so the
    audit row carries IP/User-Agent (previously null for these two doors)."""

    @pytest.mark.asyncio
    async def test_magic_link_forwards_request(self):
        from src.app.modules.auth.services.magic_link import MagicLinkService
        from src.app.modules.auth.services.tokens import IssuedSession, TokenIssuer

        service = MagicLinkService(db=MagicMock(), redis=MagicMock())
        request = _mock_request()
        issued = IssuedSession(access_token="a", refresh_token="r")

        with patch.object(TokenIssuer, "issue_session", new_callable=AsyncMock) as mock_issue:
            mock_issue.return_value = issued
            result = await service._create_auth_tokens(_mock_user(), request=request)

        mock_issue.assert_awaited_once()
        _, kwargs = mock_issue.call_args
        assert kwargs["channel"] == "magic_link"
        assert kwargs["request"] is request
        assert result["access_token"] == "a"

    @pytest.mark.asyncio
    async def test_magic_link_without_request_still_issues(self):
        """Request stays optional — old callers (and tests) keep working."""
        from src.app.modules.auth.services.magic_link import MagicLinkService
        from src.app.modules.auth.services.tokens import IssuedSession, TokenIssuer

        service = MagicLinkService(db=MagicMock(), redis=MagicMock())
        issued = IssuedSession(access_token="a", refresh_token="r")

        with patch.object(TokenIssuer, "issue_session", new_callable=AsyncMock) as mock_issue:
            mock_issue.return_value = issued
            await service._create_auth_tokens(_mock_user())

        _, kwargs = mock_issue.call_args
        assert kwargs["request"] is None

    @pytest.mark.asyncio
    async def test_passkey_forwards_request(self):
        from src.app.modules.auth.services.passkey import PasskeyService
        from src.app.modules.auth.services.tokens import IssuedSession, TokenIssuer

        service = PasskeyService()
        request = _mock_request()
        issued = IssuedSession(access_token="a", refresh_token="r")

        with patch.object(TokenIssuer, "issue_session", new_callable=AsyncMock) as mock_issue:
            mock_issue.return_value = issued
            result = await service._generate_auth_tokens(_mock_user(), request=request)

        mock_issue.assert_awaited_once()
        _, kwargs = mock_issue.call_args
        assert kwargs["channel"] == "passkey"
        assert kwargs["request"] is request
        assert result["access_token"] == "a"
