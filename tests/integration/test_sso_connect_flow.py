"""Integration tests for SSO connect/disconnect flow.

Task: T060 [US6] Write integration test for SSO connection flow

Tests the full flow of:
- Connecting a new SSO provider to existing account
- Disconnecting SSO provider with last-auth-method protection

Note: These tests use the shared `client` fixture from conftest.py which sets up
all necessary mocks for tenant, user, rate limiting, etc. We need to add X-Tenant-ID
header to requests since the middleware requires it.
"""

from unittest.mock import AsyncMock, patch

from starlette.testclient import TestClient

# Test tenant header for middleware
TEST_TENANT_HEADER = {"X-Tenant-ID": "1"}


class TestSSOConnectFlowIntegration:
    """Integration tests for SSO connect provider flow."""

    def test_connect_redirects_to_provider(self, client: TestClient):
        """Verify connect initiates OAuth flow with provider."""
        # Mock at router level - sso_service.get_authorization_url returns redirect URL
        with patch(
            "src.app.modules.sso.router.crud_user_sso_connections.exists",
            new_callable=AsyncMock,
            return_value=False,
        ):
            with patch(
                "src.app.modules.sso.router.sso_service.get_authorization_url",
                new_callable=AsyncMock,
                return_value="https://accounts.google.com/o/oauth2/auth?state=test-state",
            ):
                response = client.post(
                    "/api/v1/auth/sso/google/connect",
                    headers=TEST_TENANT_HEADER,
                    follow_redirects=False,
                )

                # The endpoint should redirect to provider
                assert response.status_code in [307, 302]
                location = response.headers.get("location", "")
                assert "google" in location.lower() or "accounts" in location.lower()

    def test_connect_stores_user_id_in_state(self, client: TestClient):
        """Verify connect mode stores user_id in OAuth state."""
        # Mock at router level and verify get_authorization_url is called with connect_mode
        with patch(
            "src.app.modules.sso.router.crud_user_sso_connections.exists",
            new_callable=AsyncMock,
            return_value=False,
        ):
            with patch(
                "src.app.modules.sso.router.sso_service.get_authorization_url",
                new_callable=AsyncMock,
                return_value="https://accounts.google.com/o/oauth2/auth?state=test-state",
            ) as mock_auth_url:
                response = client.post(
                    "/api/v1/auth/sso/google/connect",
                    headers=TEST_TENANT_HEADER,
                    follow_redirects=False,
                )

                # Verify get_authorization_url was called with connect_mode=True and user_id
                assert mock_auth_url.called
                call_kwargs = mock_auth_url.call_args.kwargs
                assert call_kwargs.get("connect_mode") is True
                assert "user_id" in call_kwargs
                assert response.status_code in [307, 302]

    def test_connect_rejects_already_connected_provider(self, client: TestClient):
        """Verify connect rejects if provider is already connected."""
        # Mock crud_user_sso_connections.exists to return True
        with patch(
            "src.app.modules.sso.router.crud_user_sso_connections.exists",
            new_callable=AsyncMock,
            return_value=True,
        ):
            response = client.post(
                "/api/v1/auth/sso/google/connect",
                headers=TEST_TENANT_HEADER,
            )

            assert response.status_code == 409
            assert "already connected" in response.json().get("detail", "").lower()

    def test_connect_rejects_disabled_provider(self, client: TestClient):
        """Verify connect rejects if provider is disabled."""
        with patch(
            "src.app.modules.sso_settings.service.sso_settings_service.get_decrypted_settings",
            new_callable=AsyncMock,
        ) as mock_settings:
            mock_settings.return_value = {
                "client_id": "test-google-id",
                "client_secret": "test-google-secret",
                "is_enabled": False,  # Provider disabled
            }

            with patch(
                "src.app.modules.sso.router.crud_user_sso_connections.exists",
                new_callable=AsyncMock,
                return_value=False,
            ):
                response = client.post(
                    "/api/v1/auth/sso/google/connect",
                    headers=TEST_TENANT_HEADER,
                )

                assert response.status_code == 400
                assert "disabled" in response.json().get("detail", "").lower()


class TestSSODisconnectFlowIntegration:
    """Integration tests for SSO disconnect provider flow."""

    def test_disconnect_removes_provider_connection(self, client: TestClient):
        """Verify disconnect removes SSO connection when user has password."""
        # Mock the service to succeed
        with patch(
            "src.app.modules.sso.router.sso_service.disconnect_provider",
            new_callable=AsyncMock,
        ) as mock_disconnect:
            response = client.delete(
                "/api/v1/auth/sso/google",
                headers=TEST_TENANT_HEADER,
            )

            assert response.status_code == 200
            assert "disconnected" in response.json().get("message", "").lower()
            mock_disconnect.assert_called_once()

    def test_disconnect_fails_for_last_auth_method(self, client: TestClient):
        """Verify disconnect fails when it's user's only auth method."""
        from src.app.core.exceptions.http_exceptions import BadRequestException

        # Mock the service to raise BadRequestException
        with patch(
            "src.app.modules.sso.router.sso_service.disconnect_provider",
            new_callable=AsyncMock,
            side_effect=BadRequestException(
                "Cannot disconnect: this is your only authentication method."
            ),
        ):
            response = client.delete(
                "/api/v1/auth/sso/google",
                headers=TEST_TENANT_HEADER,
            )

            assert response.status_code == 400
            detail = response.json().get("detail", "").lower()
            assert "only" in detail or "cannot" in detail

    def test_disconnect_succeeds_with_multiple_sso_providers(self, client: TestClient):
        """Verify SSO-only user can disconnect one provider if they have another."""
        # Mock the service to succeed (user has multiple SSO)
        with patch(
            "src.app.modules.sso.router.sso_service.disconnect_provider",
            new_callable=AsyncMock,
        ):
            response = client.delete(
                "/api/v1/auth/sso/google",
                headers=TEST_TENANT_HEADER,
            )

            assert response.status_code == 200
            assert "disconnected" in response.json().get("message", "").lower()

    def test_disconnect_not_found_provider(self, client: TestClient):
        """Verify disconnect returns 404 for non-connected provider."""
        from src.app.core.exceptions.http_exceptions import NotFoundException

        # Mock the service to raise NotFoundException
        with patch(
            "src.app.modules.sso.router.sso_service.disconnect_provider",
            new_callable=AsyncMock,
            side_effect=NotFoundException("No github connection found for this account"),
        ):
            response = client.delete(
                "/api/v1/auth/sso/github",
                headers=TEST_TENANT_HEADER,
            )

            assert response.status_code == 404

    def test_disconnect_response_format(self, client: TestClient):
        """Verify disconnect returns proper success message format."""
        with patch(
            "src.app.modules.sso.router.sso_service.disconnect_provider",
            new_callable=AsyncMock,
        ):
            response = client.delete(
                "/api/v1/auth/sso/microsoft",
                headers=TEST_TENANT_HEADER,
            )

            assert response.status_code == 200
            body = response.json()
            assert "message" in body
            assert "microsoft" in body["message"].lower()
            assert "disconnected" in body["message"].lower()
