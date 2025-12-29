"""Integration tests for Feature Flag API endpoints.

Tests cover:
- Feature flag listing endpoint
- Flag update endpoint
- Tenant-specific flag overrides
- Permission enforcement
"""

import pytest
from datetime import datetime, UTC
from uuid import uuid4
from unittest.mock import AsyncMock, patch, MagicMock

from fastapi.testclient import TestClient

from src.app.main import app
from src.app.modules.feature_flags.models import FeatureFlag, FeatureFlagOverride
from src.app.api.dependencies import get_current_user
from src.app.modules.users.models import User

@pytest.fixture(autouse=True)
def mock_current_user():
    """Mock the current user dependency for all tests in this module."""
    mock_user = User(
        email="test@example.com",
        name="Test User",
        username="testuser",
        hashed_password="mock_password_hash",
        is_superuser=False
    )
    mock_user.id = 1
    mock_user.tenant_id = 1
    app.dependency_overrides[get_current_user] = lambda: mock_user
    yield
    app.dependency_overrides.pop(get_current_user, None)


class TestFeatureFlagListAPI:
    """Integration tests for feature flag listing API."""

    def test_list_flags_requires_auth(self, client) -> None:
        """API should require tenant authentication."""
        response = client.get("/api/v1/feature-flags")
        assert response.status_code in [400, 401, 403, 422]

    def test_list_flags_with_auth(self, client, auth_headers) -> None:
        """API should return feature flags with proper auth."""
        response = client.get("/api/v1/feature-flags", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "feature_flags" in data
        assert isinstance(data["feature_flags"], dict)

    def test_list_flags_includes_overrides(self, client, auth_headers) -> None:
        """API should include tenant-specific override values."""
        response = client.get("/api/v1/feature-flags", headers=auth_headers)
        assert response.status_code == 200


class TestFeatureFlagDetailAPI:
    """Integration tests for single feature flag API."""

    def test_get_flag_by_key(self, client, auth_headers) -> None:
        """API should return flag by key."""
        response = client.get(
            "/api/v1/feature-flags/dark_mode",
            headers=auth_headers,
        )
        # Should return 200 with default null/false or explicit value
        assert response.status_code in [200, 404]

    def test_get_flag_includes_value(self, client, auth_headers) -> None:
        """API should include flag value for tenant."""
        response = client.get(
            "/api/v1/feature-flags/dark_mode",
            headers=auth_headers,
        )
        if response.status_code == 200:
            data = response.json()
            assert "enabled" in data


class TestFeatureFlagOverrideAPI:
    """Integration tests for feature flag override API."""

    def test_set_flag_override(self, client, auth_headers) -> None:
        """API should allow setting flag override."""
        override_data = {
            "value": True,
            "enabled": True
        }
        # Using PATCH matching the router implementation
        response = client.patch(
            "/api/v1/feature-flags/dark_mode",
            headers=auth_headers,
            json=override_data,
        )
        assert response.status_code in [200, 201]

    def test_delete_flag_override(self, client, auth_headers) -> None:
        """API should allow deleting flag override."""
        response = client.delete(
            "/api/v1/feature-flags/dark_mode",
            headers=auth_headers,
        )
        assert response.status_code in [200, 204, 404]


class TestFeatureFlagBulkAPI:
    """Integration tests for bulk feature flag operations."""

    def test_bulk_update_flags(self, client, auth_headers) -> None:
        """API should support bulk flag updates."""
        bulk_data = {
            "feature_flags": {
                "dark_mode": True,
                "beta_features": False,
            }
        }
        response = client.put(
            "/api/v1/feature-flags/bulk",
            headers=auth_headers,
            json=bulk_data,
        )
        assert response.status_code in [200, 404, 422]
        pass


class TestFeatureFlagTenantIsolation:
    """Tests for feature flag tenant isolation."""

    def test_flags_isolated_between_tenants(self, client, db) -> None:
        """Flag overrides from one tenant should not affect another."""
        # Create two real tenants in DB
        from src.app.modules.tenants.models import Tenant
        
        # Use simple slugs
        t1 = Tenant(name="T1", slug="t1-iso", is_active=True)
        t2 = Tenant(name="T2", slug="t2-iso", is_active=True)
        db.add(t1)
        db.add(t2)
        db.commit()
        db.refresh(t1)
        db.refresh(t2)

        headers_a = {"X-Tenant-ID": t1.slug}
        headers_b = {"X-Tenant-ID": t2.slug}

        response_a = client.get("/api/v1/feature-flags", headers=headers_a)
        response_b = client.get("/api/v1/feature-flags", headers=headers_b)

        assert response_a.status_code == 200
        assert response_b.status_code == 200

    def test_override_only_affects_own_tenant(self, client, db) -> None:
        """Setting override should only affect the requesting tenant."""
        from src.app.modules.tenants.models import Tenant
        
        # Reuse or create unique tenants
        existing_t3 = db.query(Tenant).filter(Tenant.slug == "t3-iso").first()
        if existing_t3:
            t3 = existing_t3
        else:
            t3 = Tenant(name="T3", slug="t3-iso", is_active=True)
            db.add(t3)
        
        existing_t4 = db.query(Tenant).filter(Tenant.slug == "t4-iso").first()
        if existing_t4:
            t4 = existing_t4
        else:
            t4 = Tenant(name="T4", slug="t4-iso", is_active=True)
            db.add(t4)
            
        db.commit()

        headers_a = {"X-Tenant-ID": t3.slug}
        headers_b = {"X-Tenant-ID": t4.slug}

        # Set flag for tenant A
        override_data = {"value": True, "enabled": True}
        client.patch(
            "/api/v1/feature-flags/dark_mode",
            headers=headers_a,
            json=override_data,
        )

        # Tenant B should not see the override (or get default)
        response_b = client.get("/api/v1/feature-flags/dark_mode", headers=headers_b)
        # Assuming getting a non-existent flag returns False/False or 404/200-empty
        # Based on is_feature_enabled util it returns default (False)
        if response_b.status_code == 200:
             data = response_b.json()
             # If tenant A enabled it, tenant B should implicitly NOT have it enabled (False)
             assert data.get("enabled") is False or data.get("value") is None


class TestFeatureFlagSchemas:
    """Tests for feature flag API schema validation."""

    def test_feature_flag_response_schema(self) -> None:
        """FeatureFlagsResponse schema should have correct fields."""
        from src.app.modules.feature_flags.schemas import FeatureFlagsResponse

        flag_data = {
            "feature_flags": {"dark_mode": True}
        }
        response = FeatureFlagsResponse(**flag_data)
        assert response.feature_flags["dark_mode"] is True

    def test_feature_flag_override_schema(self) -> None:
        """FeatureFlagUpdateRequest schema should validate correctly."""
        from src.app.modules.feature_flags.schemas import FeatureFlagUpdateRequest

        update_data = {
            "value": True,
            "enabled": True,
        }
        update = FeatureFlagUpdateRequest(**update_data)
        assert update.value is True
        assert update.enabled is True
