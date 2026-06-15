"""Contract tests for User Preferences API.

Task: T085 [P] [US9] Write contract tests for /user/me/preferences endpoints
"""

from fastapi.testclient import TestClient


class TestUserPreferencesAPIContract:
    """Contract tests verifying API endpoints match spec."""

    def test_preferences_endpoints_exist(self, client: TestClient, auth_headers: dict):
        """Verify that preferences endpoints exist and require authentication."""
        # GET preferences - should not return 404
        response = client.get("/api/v1/user/me/preferences", headers=auth_headers)
        assert response.status_code != 404, "GET /user/me/preferences not found"

        # PATCH preferences - should not return 404
        response = client.patch(
            "/api/v1/user/me/preferences",
            headers=auth_headers,
            json={"theme": "dark"},
        )
        assert response.status_code != 404, "PATCH /user/me/preferences not found"

    def test_preferences_requires_authentication(self, unauthenticated_client: TestClient):
        """Verify preferences endpoints require authentication."""
        # GET without auth, but WITH tenant header
        # TenantMiddleware runs before auth, so we need satisfied tenant check to reach auth check
        response = unauthenticated_client.get(
            "/api/v1/user/me/preferences",
            headers={"X-Tenant-ID": "test-tenant-integration"},
        )
        assert response.status_code == 401, f"Expected 401, got {response.status_code}: {response.text}"

        # PATCH without auth
        response = unauthenticated_client.patch(
            "/api/v1/user/me/preferences",
            json={"theme": "dark"},
            headers={"X-Tenant-ID": "test-tenant-integration"},
        )
        assert response.status_code == 401, f"Expected 401, got {response.status_code}: {response.text}"


class TestUserPreferencesGetContract:
    """Contract tests for GET /user/me/preferences."""

    def test_get_returns_all_preference_fields(self, client: TestClient, auth_headers: dict):
        """Verify GET response contains all expected fields per API spec."""
        response = client.get("/api/v1/user/me/preferences", headers=auth_headers)

        # Should succeed
        assert response.status_code == 200

        data = response.json()

        # Verify all required fields present per user-api.yaml
        required_fields = [
            "theme",
            "language",
            "email_notifications",
            "push_notifications",
            "timezone",
            "date_format",
            "currency",
            "updated_at",
        ]

        for field in required_fields:
            assert field in data, f"Response missing required field: {field}"

    def test_get_returns_correct_types(self, client: TestClient, auth_headers: dict):
        """Verify GET response field types match spec."""
        response = client.get("/api/v1/user/me/preferences", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()

        # Check types
        assert isinstance(data["theme"], str)
        assert isinstance(data["language"], str)
        assert isinstance(data["email_notifications"], bool)
        assert isinstance(data["push_notifications"], bool)
        assert isinstance(data["timezone"], str)
        assert isinstance(data["date_format"], str)
        assert isinstance(data["currency"], str)
        assert isinstance(data["updated_at"], str)  # ISO datetime string

    def test_get_returns_valid_theme_value(self, client: TestClient, auth_headers: dict):
        """Verify theme is one of the valid enum values."""
        response = client.get("/api/v1/user/me/preferences", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()

        valid_themes = ["system", "light", "dark"]
        assert data["theme"] in valid_themes


class TestUserPreferencesPatchContract:
    """Contract tests for PATCH /user/me/preferences."""

    def test_patch_validates_theme(self, client: TestClient, auth_headers: dict):
        """Verify PATCH validates theme enum."""
        # Invalid theme
        response = client.patch(
            "/api/v1/user/me/preferences",
            headers=auth_headers,
            json={"theme": "invalid_theme"},
        )
        assert response.status_code == 422  # Validation error

        # Valid theme
        response = client.patch(
            "/api/v1/user/me/preferences",
            headers=auth_headers,
            json={"theme": "dark"},
        )
        assert response.status_code == 200

    def test_patch_validates_language_code(self, client: TestClient, auth_headers: dict):
        """Verify language must be 2-char ISO code."""
        # Invalid - too long
        response = client.patch(
            "/api/v1/user/me/preferences",
            headers=auth_headers,
            json={"language": "eng"},
        )
        assert response.status_code == 422

        # Valid
        response = client.patch(
            "/api/v1/user/me/preferences",
            headers=auth_headers,
            json={"language": "en"},
        )
        assert response.status_code == 200

    def test_patch_validates_currency_code(self, client: TestClient, auth_headers: dict):
        """Verify currency must be 3-char ISO code."""
        # Invalid - lowercase
        response = client.patch(
            "/api/v1/user/me/preferences",
            headers=auth_headers,
            json={"currency": "usd"},
        )
        assert response.status_code == 422

        # Valid
        response = client.patch(
            "/api/v1/user/me/preferences",
            headers=auth_headers,
            json={"currency": "EUR"},
        )
        assert response.status_code == 200

    def test_patch_allows_partial_updates(self, client: TestClient, auth_headers: dict):
        """Verify PATCH accepts partial update (not all fields required)."""
        # Update only one field
        response = client.patch(
            "/api/v1/user/me/preferences",
            headers=auth_headers,
            json={"email_notifications": False},
        )
        assert response.status_code == 200

        # Empty body should also be valid
        response = client.patch(
            "/api/v1/user/me/preferences",
            headers=auth_headers,
            json={},
        )
        assert response.status_code == 200

    def test_patch_returns_updated_preferences(self, client: TestClient, auth_headers: dict):
        """Verify PATCH returns the full updated preferences."""
        response = client.patch(
            "/api/v1/user/me/preferences",
            headers=auth_headers,
            json={"push_notifications": True},
        )

        assert response.status_code == 200
        data = response.json()

        # Should contain all fields, not just updated one
        assert "theme" in data
        assert "language" in data
        assert "push_notifications" in data
        assert data["push_notifications"] is True
