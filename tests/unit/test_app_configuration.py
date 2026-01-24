"""
App Configuration Tests

Unit tests for the app configuration module.
"""

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from src.app.main import app

client = TestClient(app)


class TestAppConfiguration:
    """Test app configuration endpoint."""

    def test_get_app_configuration(self):
        """Should return app configuration without authentication."""
        response = client.get("/api/v1/app_configuration")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # Check required fields
        assert "app_name" in data
        assert "app_version" in data
        assert "environment" in data
        assert "theme" in data
        assert "features" in data
        assert "api" in data
        assert "maintenance_mode" in data

    def test_configuration_theme_structure(self):
        """Theme configuration should have correct structure."""
        response = client.get("/api/v1/app_configuration")
        data = response.json()

        theme = data["theme"]
        assert "primary_color" in theme
        assert "secondary_color" in theme
        assert "dark_mode_enabled" in theme
        assert "default_theme" in theme

        # Validate color format (should be hex)
        assert theme["primary_color"].startswith("#")
        assert len(theme["primary_color"]) == 7

    def test_configuration_features_structure(self):
        """Features configuration should have correct structure."""
        response = client.get("/api/v1/app_configuration")
        data = response.json()

        features = data["features"]

        # Check all feature flags exist
        expected_features = [
            "sso_enabled",
            "passkey_enabled",
            "magic_link_enabled",
            "billing_enabled",
            "ai_enabled",
            "webhooks_enabled",
            "storage_enabled",
            "audit_logs_enabled",
            "multi_tenancy_enabled",
        ]

        for feature in expected_features:
            assert feature in features
            assert isinstance(features[feature], bool)

    def test_configuration_api_structure(self):
        """API configuration should have correct structure."""
        response = client.get("/api/v1/app_configuration")
        data = response.json()

        api = data["api"]
        assert "version" in api
        assert "base_url" in api
        assert "websocket_url" in api

        # Validate values
        assert api["base_url"] == "/api/v1"
        assert api["version"] is not None

    def test_configuration_is_public(self):
        """App configuration should be accessible without authentication."""
        # Should work without Authorization header
        response = client.get("/api/v1/app_configuration")
        assert response.status_code == status.HTTP_200_OK

    def test_configuration_openapi_spec(self):
        """App configuration should be documented in OpenAPI spec."""
        response = client.get("/openapi.json")
        assert response.status_code == 200

        spec = response.json()
        paths = spec["paths"]

        # Check endpoint exists
        assert "/api/v1/app_configuration" in paths
        assert "get" in paths["/api/v1/app_configuration"]

        # Check tags
        endpoint = paths["/api/v1/app_configuration"]["get"]
        assert "configuration" in endpoint["tags"]

    def test_configuration_response_model(self):
        """Response should match AppConfigurationResponse schema."""
        response = client.get("/api/v1/app_configuration")
        data = response.json()

        # Basic type validation
        assert isinstance(data["app_name"], str)
        assert isinstance(data["app_version"], str)
        assert isinstance(data["environment"], str)
        assert isinstance(data["theme"], dict)
        assert isinstance(data["features"], dict)
        assert isinstance(data["api"], dict)
        assert isinstance(data["maintenance_mode"], bool)


class TestAppConfigurationService:
    """Test app configuration service logic."""

    @pytest.mark.asyncio
    async def test_service_returns_configuration(self):
        """Service should return valid configuration."""
        from src.app.modules.app_configuration.service import app_configuration_service

        config = await app_configuration_service.get_configuration()

        assert config.app_name is not None
        assert config.app_version is not None
        assert config.environment is not None
        assert config.theme is not None
        assert config.features is not None
        assert config.api is not None
        assert isinstance(config.maintenance_mode, bool)

    @pytest.mark.asyncio
    async def test_service_features_all_enabled(self):
        """All features should be enabled by default."""
        from src.app.modules.app_configuration.service import app_configuration_service

        config = await app_configuration_service.get_configuration()
        features = config.features

        # All features should be True (all modules exist)
        assert features.sso_enabled is True
        assert features.passkey_enabled is True
        assert features.magic_link_enabled is True
        assert features.billing_enabled is True
        assert features.ai_enabled is True
        assert features.webhooks_enabled is True
        assert features.storage_enabled is True
        assert features.audit_logs_enabled is True
        assert features.multi_tenancy_enabled is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
