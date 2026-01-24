"""
Dashboard Tests

Unit tests for the dashboard module.
"""

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from src.app.main import app

client = TestClient(app)


class TestDashboardEndpoint:
    """Test dashboard metrics endpoint."""

    def test_dashboard_requires_tenant(self):
        """Should return 400 when tenant header missing."""
        response = client.get("/api/v1/dashboard")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Missing required header" in response.json()["detail"]

    def test_dashboard_with_time_range_params(self):
        """Should accept time_range query parameter."""
        # Without auth, should still validate params
        response = client.get("/api/v1/dashboard?time_range=week")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_dashboard_registered_in_openapi(self):
        """Dashboard endpoint should be in OpenAPI spec."""
        response = client.get("/openapi.json")
        assert response.status_code == 200

        spec = response.json()
        paths = spec["paths"]

        # Check endpoint exists
        assert "/api/v1/dashboard" in paths
        assert "get" in paths["/api/v1/dashboard"]

        # Check tags
        endpoint = paths["/api/v1/dashboard"]["get"]
        assert "dashboard" in endpoint["tags"]

    def test_dashboard_time_range_enum(self):
        """Dashboard should document time_range enum values."""
        response = client.get("/openapi.json")
        spec = response.json()

        endpoint = spec["paths"]["/api/v1/dashboard"]["get"]
        params = endpoint["parameters"]

        # Find time_range parameter
        time_range_param = next((p for p in params if p.get("name") == "time_range"), None)

        assert time_range_param is not None
        assert "schema" in time_range_param


class TestDashboardSchemas:
    """Test dashboard schema structures."""

    def test_time_range_enum_values(self):
        """TimeRange enum should have expected values."""
        from src.app.modules.dashboard.schemas import TimeRange

        expected_values = ["today", "week", "month", "quarter", "year", "all"]
        actual_values = [tr.value for tr in TimeRange]

        for expected in expected_values:
            assert expected in actual_values

    def test_user_metrics_schema(self):
        """UserMetrics schema should have correct fields."""
        from src.app.modules.dashboard.schemas import UserMetrics

        # Should be able to create instance
        metrics = UserMetrics(
            total_users=100,
            active_users=80,
            new_users=10,
            deleted_users=2,
        )

        assert metrics.total_users == 100
        assert metrics.active_users == 80
        assert metrics.new_users == 10
        assert metrics.deleted_users == 2

    def test_revenue_metrics_schema(self):
        """RevenueMetrics schema should have correct fields."""
        from src.app.modules.dashboard.schemas import RevenueMetrics

        metrics = RevenueMetrics(
            total_revenue=1000.0,
            recurring_revenue=800.0,
            average_revenue_per_user=12.50,
            total_subscriptions=50,
        )

        assert metrics.total_revenue == 1000.0
        assert metrics.recurring_revenue == 800.0
        assert metrics.average_revenue_per_user == 12.50
        assert metrics.total_subscriptions == 50

    def test_activity_metrics_schema(self):
        """ActivityMetrics schema should have correct fields."""
        from src.app.modules.dashboard.schemas import ActivityMetrics

        metrics = ActivityMetrics(
            total_logins=500,
            api_calls=10000,
            webhook_deliveries=100,
            failed_authentications=5,
        )

        assert metrics.total_logins == 500
        assert metrics.api_calls == 10000
        assert metrics.webhook_deliveries == 100
        assert metrics.failed_authentications == 5


class TestDashboardService:
    """Test dashboard service logic."""

    @pytest.mark.asyncio
    async def test_get_date_range_month(self):
        """Service should calculate correct date range for month."""
        from src.app.modules.dashboard.schemas import TimeRange
        from src.app.modules.dashboard.service import dashboard_service

        start_date, end_date = dashboard_service._get_date_range(TimeRange.MONTH)

        # Should be approximately 30 days
        delta = end_date - start_date
        assert 29 <= delta.days <= 31

    @pytest.mark.asyncio
    async def test_get_date_range_week(self):
        """Service should calculate correct date range for week."""
        from src.app.modules.dashboard.schemas import TimeRange
        from src.app.modules.dashboard.service import dashboard_service

        start_date, end_date = dashboard_service._get_date_range(TimeRange.WEEK)

        # Should be 7 days
        delta = end_date - start_date
        assert delta.days == 7

    @pytest.mark.asyncio
    async def test_get_date_range_today(self):
        """Service should calculate correct date range for today."""
        from src.app.modules.dashboard.schemas import TimeRange
        from src.app.modules.dashboard.service import dashboard_service

        start_date, end_date = dashboard_service._get_date_range(TimeRange.TODAY)

        # Start should be beginning of today
        assert start_date.hour == 0
        assert start_date.minute == 0
        assert start_date.second == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
