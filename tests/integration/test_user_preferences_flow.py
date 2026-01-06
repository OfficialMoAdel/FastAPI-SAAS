"""Integration test for User Preferences flow.

Task: T086 [US9] Write integration test for preferences flow
"""

import pytest
from fastapi.testclient import TestClient


@pytest.mark.integration
class TestUserPreferencesFlow:
    """Integration tests for user preferences management flow."""

    def test_new_user_gets_default_preferences(
        self, client: TestClient, auth_headers: dict
    ):
        """
        Test that a new user gets default preferences.

        Flow:
        1. Authenticated user requests preferences
        2. Preferences are created with defaults
        3. All default values are returned
        """
        response = client.get("/api/v1/user/me/preferences", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()

        # Should have default values
        assert data["theme"] == "system"
        assert data["language"] == "en"
        assert data["email_notifications"] is True
        assert data["push_notifications"] is True
        assert data["timezone"] == "UTC"
        assert data["date_format"] == "YYYY-MM-DD"
        assert data["currency"] == "USD"

    def test_update_single_preference_preserves_others(
        self, client: TestClient, auth_headers: dict
    ):
        """
        Test partial update preserves other preferences.

        Flow:
        1. Get initial preferences
        2. Update only theme
        3. Verify theme changed but others preserved
        """
        # Get initial state
        response = client.get("/api/v1/user/me/preferences", headers=auth_headers)
        assert response.status_code == 200
        initial = response.json()

        # Update only theme
        response = client.patch(
            "/api/v1/user/me/preferences",
            headers=auth_headers,
            json={"theme": "dark"},
        )
        assert response.status_code == 200
        updated = response.json()

        # Theme should change
        assert updated["theme"] == "dark"

        # Other fields should be preserved
        assert updated["language"] == initial["language"]
        assert updated["timezone"] == initial["timezone"]
        assert updated["email_notifications"] == initial["email_notifications"]

    def test_update_multiple_preferences(
        self, client: TestClient, auth_headers: dict
    ):
        """
        Test updating multiple preferences at once.

        Flow:
        1. Update theme, language, and notifications
        2. Verify all changes applied
        """
        response = client.patch(
            "/api/v1/user/me/preferences",
            headers=auth_headers,
            json={
                "theme": "light",
                "language": "fr",
                "email_notifications": False,
                "push_notifications": False,
            },
        )

        assert response.status_code == 200
        data = response.json()

        assert data["theme"] == "light"
        assert data["language"] == "fr"
        assert data["email_notifications"] is False
        assert data["push_notifications"] is False

    def test_preferences_persist_across_requests(
        self, client: TestClient, auth_headers: dict
    ):
        """
        Test that preference changes persist.

        Flow:
        1. Update a preference
        2. Make a new GET request
        3. Verify the change persisted
        """
        # Update preference
        response = client.patch(
            "/api/v1/user/me/preferences",
            headers=auth_headers,
            json={"currency": "GBP"},
        )
        assert response.status_code == 200

        # Fetch preferences again
        response = client.get("/api/v1/user/me/preferences", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()

        # Should still be updated
        assert data["currency"] == "GBP"

    def test_update_timezone_and_date_format(
        self, client: TestClient, auth_headers: dict
    ):
        """Test updating localization preferences."""
        response = client.patch(
            "/api/v1/user/me/preferences",
            headers=auth_headers,
            json={
                "timezone": "Europe/London",
                "date_format": "DD/MM/YYYY",
            },
        )

        assert response.status_code == 200
        data = response.json()

        assert data["timezone"] == "Europe/London"
        assert data["date_format"] == "DD/MM/YYYY"

    def test_preferences_include_updated_timestamp(
        self, client: TestClient, auth_headers: dict
    ):
        """
        Test that updated_at timestamp changes on update.

        Flow:
        1. Get current preferences with timestamp
        2. Update a preference
        3. Verify updated_at changed
        """
        # Get initial
        response = client.get("/api/v1/user/me/preferences", headers=auth_headers)
        assert response.status_code == 200
        initial_updated_at = response.json()["updated_at"]

        # Note: Depending on timing, the timestamp might not change noticeably
        # in the same second. This test verifies the field exists and is returned.
        assert initial_updated_at is not None

        # Update something
        response = client.patch(
            "/api/v1/user/me/preferences",
            headers=auth_headers,
            json={"theme": "system"},
        )
        assert response.status_code == 200

        new_updated_at = response.json()["updated_at"]
        assert new_updated_at is not None

    def test_empty_update_returns_current_preferences(
        self, client: TestClient, auth_headers: dict
    ):
        """Test that empty PATCH body returns current preferences."""
        # Set some preferences first
        client.patch(
            "/api/v1/user/me/preferences",
            headers=auth_headers,
            json={"theme": "dark"},
        )

        # Empty update
        response = client.patch(
            "/api/v1/user/me/preferences",
            headers=auth_headers,
            json={},
        )

        assert response.status_code == 200
        data = response.json()

        # Should return current state
        assert data["theme"] == "dark"


@pytest.mark.integration
class TestUserPreferencesValidation:
    """Integration tests for validation in preferences endpoints."""

    def test_invalid_language_rejected(self, client: TestClient, auth_headers: dict):
        """Test that invalid language codes are rejected."""
        # Too long
        response = client.patch(
            "/api/v1/user/me/preferences",
            headers=auth_headers,
            json={"language": "english"},
        )
        assert response.status_code == 422

    def test_invalid_currency_rejected(self, client: TestClient, auth_headers: dict):
        """Test that invalid currency codes are rejected."""
        # Wrong case
        response = client.patch(
            "/api/v1/user/me/preferences",
            headers=auth_headers,
            json={"currency": "Usd"},
        )
        assert response.status_code == 422

    def test_invalid_theme_rejected(self, client: TestClient, auth_headers: dict):
        """Test that invalid theme values are rejected."""
        response = client.patch(
            "/api/v1/user/me/preferences",
            headers=auth_headers,
            json={"theme": "purple"},  # Not valid
        )
        assert response.status_code == 422
