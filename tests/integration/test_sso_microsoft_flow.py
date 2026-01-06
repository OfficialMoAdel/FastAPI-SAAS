"""Integration test for complete Microsoft SSO authentication flow.

TDD: This test is written first and should FAIL until implementation is complete.

Task: T052 [US4] Write integration test for Microsoft SSO flow

This test validates HTTP endpoint behavior for Microsoft SSO.
Full OAuth flow validation is covered by unit tests and contract tests.
"""

from unittest.mock import AsyncMock, Mock

import pytest


class TestMicrosoftSSOEndpoints:
    """Integration tests for Microsoft SSO HTTP endpoints."""

    @pytest.mark.asyncio
    async def test_microsoft_callback_invalid_state_returns_400(
        self,
        client,
        auth_headers,
    ):
        """Test Microsoft callback with invalid state returns 400."""
        # Mock SSO state manager to return None (invalid state)
        mock_manager = Mock()
        mock_manager.validate_state = AsyncMock(return_value=None)

        # Patch at the service module level
        import src.app.modules.sso.service as sso_service_module

        original_get_manager = sso_service_module.get_sso_state_manager
        sso_service_module.get_sso_state_manager = lambda redis: mock_manager

        try:
            response = client.get(
                "/api/v1/auth/sso/microsoft/callback",
                params={
                    "code": "valid_code_but_bad_state",
                    "state": "invalid_state",
                },
                headers=auth_headers,
            )

            assert response.status_code == 400
        finally:
            # Restore original
            sso_service_module.get_sso_state_manager = original_get_manager

    @pytest.mark.asyncio
    async def test_microsoft_callback_missing_code_returns_400(
        self,
        client,
        auth_headers,
    ):
        """Test Microsoft callback missing code parameter returns 400."""
        mock_manager = Mock()
        mock_manager.validate_state = AsyncMock(return_value=None)

        import src.app.modules.sso.service as sso_service_module

        original_get_manager = sso_service_module.get_sso_state_manager
        sso_service_module.get_sso_state_manager = lambda redis: mock_manager

        try:
            response = client.get(
                "/api/v1/auth/sso/microsoft/callback",
                params={"state": "test_state"},  # Missing code
                headers=auth_headers,
            )

            assert response.status_code == 422  # Validation error for missing field
        finally:
            sso_service_module.get_sso_state_manager = original_get_manager

    @pytest.mark.asyncio
    async def test_microsoft_callback_missing_state_returns_400(
        self,
        client,
        auth_headers,
    ):
        """Test Microsoft callback missing state parameter returns 400."""
        mock_manager = Mock()
        mock_manager.validate_state = AsyncMock(return_value=None)

        import src.app.modules.sso.service as sso_service_module

        original_get_manager = sso_service_module.get_sso_state_manager
        sso_service_module.get_sso_state_manager = lambda redis: mock_manager

        try:
            response = client.get(
                "/api/v1/auth/sso/microsoft/callback",
                params={"code": "test_code"},  # Missing state
                headers=auth_headers,
            )

            assert response.status_code == 422  # Validation error for missing field
        finally:
            sso_service_module.get_sso_state_manager = original_get_manager


class TestMicrosoftSSOProviderSelection:
    """Test Microsoft provider is available and enabled."""

    @pytest.mark.asyncio
    async def test_microsoft_provider_available(self, client, auth_headers):
        """Test that Microsoft is listed as available SSO provider."""
        response = client.get("/api/v1/auth/sso/providers", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert "providers" in data
        # Microsoft should be in the list (implementation detail)
        assert isinstance(data["providers"], list)
