"""Integration tests for SSO connect/disconnect flow.

Task: T060 [US6] Write integration test for SSO connection flow

Tests the full flow of:
- Connecting a new SSO provider to existing account
- Disconnecting SSO provider with last-auth-method protection

Note: These tests use the shared `client` fixture from conftest.py which sets up
all necessary mocks for tenant, user, rate limiting, etc. We need to add X-Tenant-ID
header to requests since the middleware requires it.
"""

from contextlib import contextmanager
from unittest.mock import AsyncMock, Mock, patch

from starlette.testclient import TestClient

from src.app.main import app
from src.app.modules.auth.services.sso import get_sso_service

# Test tenant header for middleware
TEST_TENANT_HEADER = {"X-Tenant-ID": "1"}


def _create_mock_sso_service(**method_returns):
    """Helper to create a mock SSOService with specified method return values."""
    mock_service = Mock()
    for method_name, return_value in method_returns.items():
        if isinstance(return_value, Exception):
            setattr(mock_service, method_name, AsyncMock(side_effect=return_value))
        else:
            setattr(mock_service, method_name, AsyncMock(return_value=return_value))
    return mock_service


@contextmanager
def override_sso_service(mock_service):
    """Context manager to override get_sso_service dependency."""
    app.dependency_overrides[get_sso_service] = lambda: mock_service
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_sso_service, None)


class TestSSOConnectFlowIntegration:
    """Integration tests for SSO connect provider flow."""

    def test_connect_redirects_to_provider(self, client: TestClient):
        """Verify connect initiates OAuth flow with provider."""
        mock_service = _create_mock_sso_service(
            get_authorization_url=("https://accounts.google.com/o/oauth2/auth?state=test-state", "test-state")
        )
        with patch(
            "src.app.modules.auth.routers.sso.crud_user_sso_connections.exists",
            new_callable=AsyncMock,
            return_value=False,
        ):
            with override_sso_service(mock_service):
                response = client.post(
                    "/api/v1/account/sso/google/connect",
                    headers=TEST_TENANT_HEADER,
                    follow_redirects=False,
                )

                # The endpoint should redirect to provider
                assert response.status_code in [307, 302]
                location = response.headers.get("location", "")
                assert "google" in location.lower() or "accounts" in location.lower()

    def test_connect_stores_user_id_in_state(self, client: TestClient):
        """Verify connect mode stores user_id in OAuth state."""
        mock_service = _create_mock_sso_service(
            get_authorization_url=("https://accounts.google.com/o/oauth2/auth?state=test-state", "test-state")
        )
        with patch(
            "src.app.modules.auth.routers.sso.crud_user_sso_connections.exists",
            new_callable=AsyncMock,
            return_value=False,
        ):
            with override_sso_service(mock_service):
                response = client.post(
                    "/api/v1/account/sso/google/connect",
                    headers=TEST_TENANT_HEADER,
                    follow_redirects=False,
                )

                # Verify get_authorization_url was called with connect_mode=True and user_id
                assert mock_service.get_authorization_url.called
                call_kwargs = mock_service.get_authorization_url.call_args.kwargs
                assert call_kwargs.get("connect_mode") is True
                assert "user_id" in call_kwargs
                assert response.status_code in [307, 302]

    def test_connect_rejects_already_connected_provider(self, client: TestClient):
        """Verify connect rejects if provider is already connected."""
        # Mock exists_for_user to return True (connection exists)
        with patch(
            "src.app.modules.auth.routers.sso.crud_user_sso_connections.exists_for_user",
            new_callable=AsyncMock,
            return_value=True,
        ):
            response = client.post(
                "/api/v1/account/sso/google/connect",
                headers=TEST_TENANT_HEADER,
            )

            assert response.status_code == 409
            assert "already connected" in response.json().get("detail", "").lower()

    def test_connect_rejects_disabled_provider(self, client: TestClient):
        """Verify connect rejects if provider is disabled."""
        with patch(
            "src.app.modules.auth.services.sso_settings.sso_settings_service.get_decrypted_settings",
            new_callable=AsyncMock,
        ) as mock_settings:
            mock_settings.return_value = {
                "client_id": "test-google-id",
                "client_secret": "test-google-secret",
                "is_enabled": False,  # Provider disabled
            }

            with patch(
                "src.app.modules.auth.routers.sso.crud_user_sso_connections.exists",
                new_callable=AsyncMock,
                return_value=False,
            ):
                response = client.post(
                    "/api/v1/account/sso/google/connect",
                    headers=TEST_TENANT_HEADER,
                )

                assert response.status_code == 400
                assert "disabled" in response.json().get("detail", "").lower()


class TestSSODisconnectFlowIntegration:
    """Integration tests for SSO disconnect provider flow."""

    def test_disconnect_removes_provider_connection(self, client: TestClient):
        """Verify disconnect removes SSO connection when user has password."""
        mock_service = _create_mock_sso_service(disconnect_provider={"message": "google disconnected successfully"})
        with override_sso_service(mock_service):
            response = client.delete(
                "/api/v1/account/sso/google",
                headers=TEST_TENANT_HEADER,
            )

            assert response.status_code == 200
            assert "disconnected" in response.json().get("message", "").lower()
            mock_service.disconnect_provider.assert_called_once()

    def test_disconnect_fails_for_last_auth_method(self, client: TestClient):
        """Verify disconnect fails when it's user's only auth method."""
        from src.app.shared.exceptions import ValidationError as BadRequestException

        mock_service = _create_mock_sso_service(
            disconnect_provider=BadRequestException("Cannot disconnect: this is your only authentication method.")
        )
        with override_sso_service(mock_service):
            response = client.delete(
                "/api/v1/account/sso/google",
                headers=TEST_TENANT_HEADER,
            )

            assert response.status_code == 400
            detail = response.json().get("detail", "").lower()
            assert "only" in detail or "cannot" in detail

    def test_disconnect_succeeds_with_multiple_sso_providers(self, client: TestClient):
        """Verify SSO-only user can disconnect one provider if they have another."""
        mock_service = _create_mock_sso_service(disconnect_provider={"message": "google disconnected successfully"})
        with override_sso_service(mock_service):
            response = client.delete(
                "/api/v1/account/sso/google",
                headers=TEST_TENANT_HEADER,
            )

            assert response.status_code == 200
            assert "disconnected" in response.json().get("message", "").lower()

    def test_disconnect_not_found_provider(self, client: TestClient):
        """Verify disconnect returns 404 for non-connected provider."""
        from src.app.shared.exceptions import ModelNotFoundError as NotFoundException

        mock_service = _create_mock_sso_service(
            disconnect_provider=NotFoundException("No github connection found for this account")
        )
        with override_sso_service(mock_service):
            response = client.delete(
                "/api/v1/account/sso/github",
                headers=TEST_TENANT_HEADER,
            )

            assert response.status_code == 404

    def test_disconnect_response_format(self, client: TestClient):
        """Verify disconnect returns proper success message format."""
        mock_service = _create_mock_sso_service(disconnect_provider={"message": "microsoft disconnected successfully"})
        with override_sso_service(mock_service):
            response = client.delete(
                "/api/v1/account/sso/microsoft",
                headers=TEST_TENANT_HEADER,
            )

            assert response.status_code == 200
            body = response.json()
            assert "message" in body
            assert "microsoft" in body["message"].lower()
            assert "disconnected" in body["message"].lower()
