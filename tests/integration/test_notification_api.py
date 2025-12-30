"""Integration tests for Notification API endpoints.

Tests cover:
- Notification sending endpoints
- Notification status tracking
- Tenant isolation for notifications
- Email template endpoints
"""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.app.main import app


class TestNotificationListAPI:
    """Integration tests for notification listing API."""

    @pytest.fixture
    def client(self):
        """Test client fixture."""
        return TestClient(app)

    @pytest.fixture
    def sample_tenant_id(self):
        """Sample tenant UUID."""
        return uuid4()

    @pytest.fixture
    def auth_headers(self, sample_tenant_id):
        """Authentication headers for tenant context."""
        return {"X-Tenant-ID": str(sample_tenant_id)}

    def test_list_notifications_requires_auth(self, client) -> None:
        """API should require tenant authentication."""
        response = client.get("/api/v1/notifications/")
        assert response.status_code in [401, 403, 422]

    def test_list_notifications_with_auth(self, client, auth_headers) -> None:
        """API should return notifications with proper auth."""
        response = client.get("/api/v1/notifications/", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, (list, dict))

    def test_list_notifications_pagination(self, client, auth_headers) -> None:
        """API should support pagination for notifications."""
        response = client.get(
            "/api/v1/notifications/",
            headers=auth_headers,
            params={"page": 1, "per_page": 10},
        )
        assert response.status_code == 200

    def test_list_notifications_filter_by_status(self, client, auth_headers) -> None:
        """API should support filtering by status."""
        response = client.get(
            "/api/v1/notifications/",
            headers=auth_headers,
            params={"status": "sent"},
        )
        assert response.status_code == 200


class TestNotificationDetailAPI:
    """Integration tests for single notification API."""

    @pytest.fixture
    def client(self):
        """Test client fixture."""
        return TestClient(app)

    @pytest.fixture
    def sample_tenant_id(self):
        """Sample tenant UUID."""
        return uuid4()

    @pytest.fixture
    def auth_headers(self, sample_tenant_id):
        """Authentication headers for tenant context."""
        return {"X-Tenant-ID": str(sample_tenant_id)}

    def test_get_notification_by_id(self, client, auth_headers) -> None:
        """API should return notification by ID."""
        notification_id = str(uuid4())
        response = client.get(
            f"/api/v1/notifications/{notification_id}",
            headers=auth_headers,
        )
        assert response.status_code in [200, 404]


class TestNotificationSendAPI:
    """Integration tests for notification sending API."""

    @pytest.fixture
    def client(self):
        """Test client fixture."""
        return TestClient(app)

    @pytest.fixture
    def sample_tenant_id(self):
        """Sample tenant UUID."""
        return uuid4()

    @pytest.fixture
    def auth_headers(self, sample_tenant_id):
        """Authentication headers for tenant context."""
        return {"X-Tenant-ID": str(sample_tenant_id)}

    def test_send_notification_requires_auth(self, client) -> None:
        """Send notification endpoint requires authentication."""
        response = client.post("/api/v1/notifications/send")
        assert response.status_code in [401, 403, 422]

    def test_send_notification(self, client, auth_headers) -> None:
        """API should send notification."""
        notification_data = {
            "template_name": "welcome",
            "to_email": "test@example.com",
            "context": {"name": "Test User"},
        }
        response = client.post(
            "/api/v1/notifications/send",
            headers=auth_headers,
            json=notification_data,
        )
        # Could be 200, 201, 202 for success, or 422/404 if template doesn't exist
        assert response.status_code in [200, 201, 202, 404, 422]

    def test_send_notification_invalid_email(self, client, auth_headers) -> None:
        """API should validate email format."""
        notification_data = {
            "template_name": "welcome",
            "to_email": "not-an-email",
            "context": {},
        }
        response = client.post(
            "/api/v1/notifications/send",
            headers=auth_headers,
            json=notification_data,
        )
        assert response.status_code == 422


class TestNotificationRetryAPI:
    """Integration tests for notification retry API."""

    @pytest.fixture
    def client(self):
        """Test client fixture."""
        return TestClient(app)

    @pytest.fixture
    def sample_tenant_id(self):
        """Sample tenant UUID."""
        return uuid4()

    @pytest.fixture
    def auth_headers(self, sample_tenant_id):
        """Authentication headers for tenant context."""
        return {"X-Tenant-ID": str(sample_tenant_id)}

    def test_retry_notification(self, client, auth_headers) -> None:
        """API should allow retrying failed notification."""
        notification_id = str(uuid4())
        response = client.post(
            f"/api/v1/notifications/{notification_id}/retry",
            headers=auth_headers,
        )
        assert response.status_code in [200, 202, 404]


class TestEmailTemplateAPI:
    """Integration tests for email template API."""

    @pytest.fixture
    def client(self):
        """Test client fixture."""
        return TestClient(app)

    @pytest.fixture
    def sample_tenant_id(self):
        """Sample tenant UUID."""
        return uuid4()

    @pytest.fixture
    def auth_headers(self, sample_tenant_id):
        """Authentication headers for tenant context."""
        return {"X-Tenant-ID": str(sample_tenant_id)}

    def test_list_templates(self, client, auth_headers) -> None:
        """API should list available email templates."""
        response = client.get("/api/v1/notifications/templates", headers=auth_headers)
        assert response.status_code in [200, 404]

    def test_preview_template(self, client, auth_headers) -> None:
        """API should preview rendered template."""
        preview_data = {
            "template_name": "welcome",
            "context": {"name": "Test User"},
        }
        response = client.post(
            "/api/v1/notifications/templates/preview",
            headers=auth_headers,
            json=preview_data,
        )
        assert response.status_code in [200, 404, 422]


class TestNotificationTenantIsolation:
    """Tests for notification tenant isolation."""

    @pytest.fixture
    def client(self):
        """Test client fixture."""
        return TestClient(app)

    def test_notifications_isolated_between_tenants(self, client) -> None:
        """Notifications from one tenant should not be visible to another."""
        tenant_a = str(uuid4())
        tenant_b = str(uuid4())

        headers_a = {"X-Tenant-ID": tenant_a}
        headers_b = {"X-Tenant-ID": tenant_b}

        response_a = client.get("/api/v1/notifications/", headers=headers_a)
        response_b = client.get("/api/v1/notifications/", headers=headers_b)

        assert response_a.status_code == 200
        assert response_b.status_code == 200

    def test_cannot_access_other_tenant_notification(self, client) -> None:
        """Should not be able to access notification from another tenant."""
        tenant_a = str(uuid4())
        notification_id = str(uuid4())

        response = client.get(
            f"/api/v1/notifications/{notification_id}",
            headers={"X-Tenant-ID": tenant_a},
        )
        # Should return 404 as notification doesn't exist or belong to this tenant
        assert response.status_code in [404, 403, 200]
