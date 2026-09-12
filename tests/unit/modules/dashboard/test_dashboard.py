"""
Dashboard Tests

Unit tests for the dashboard module.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import status


class TestDashboardEndpoint:
    """Test dashboard metrics endpoint."""

    def test_dashboard_requires_tenant(self, client):
        """Should return 400 when tenant header missing."""
        response = client.get("/api/v1/dashboard")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Missing required header" in response.json()["detail"]

    def test_dashboard_with_time_range_params(self, client):
        """Should accept time_range query parameter."""
        # Without auth, should still validate params
        response = client.get("/api/v1/dashboard?time_range=week")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_dashboard_registered_in_openapi(self, client):
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

    def test_dashboard_time_range_enum(self, client):
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
        from unittest.mock import AsyncMock

        from src.app.modules.dashboard.schemas import TimeRange
        from src.app.modules.dashboard.service import dashboard_service

        db_mock = AsyncMock()
        start_date, end_date = await dashboard_service._get_date_range(db_mock, TimeRange.MONTH, "test-tenant")

        # Should be approximately 30 days
        delta = end_date - start_date
        assert 29 <= delta.days <= 31

    @pytest.mark.asyncio
    async def test_get_date_range_week(self):
        """Service should calculate correct date range for week."""
        from unittest.mock import AsyncMock

        from src.app.modules.dashboard.schemas import TimeRange
        from src.app.modules.dashboard.service import dashboard_service

        db_mock = AsyncMock()
        start_date, end_date = await dashboard_service._get_date_range(db_mock, TimeRange.WEEK, "test-tenant")

        # Should be 7 days
        delta = end_date - start_date
        assert delta.days == 7

    @pytest.mark.asyncio
    async def test_get_date_range_today(self):
        """Service should calculate correct date range for today."""
        from unittest.mock import AsyncMock

        from src.app.modules.dashboard.schemas import TimeRange
        from src.app.modules.dashboard.service import dashboard_service

        db_mock = AsyncMock()
        start_date, end_date = await dashboard_service._get_date_range(db_mock, TimeRange.TODAY, "test-tenant")

        # Start should be beginning of today
        assert start_date.hour == 0
        assert start_date.minute == 0
        assert start_date.second == 0


class TestDashboardServiceDateRange:
    """Test all date range calculations."""

    @pytest.mark.asyncio
    async def test_date_range_today(self):
        """Test TODAY range returns start of today."""
        from src.app.modules.dashboard.schemas import TimeRange
        from src.app.modules.dashboard.service import dashboard_service

        db_mock = AsyncMock()
        start, end = await dashboard_service._get_date_range(db_mock, TimeRange.TODAY, "tenant")

        assert start.hour == 0
        assert start.minute == 0
        assert start.second == 0
        assert start.microsecond == 0

    @pytest.mark.asyncio
    async def test_date_range_week(self):
        """Test WEEK range returns 7 days."""
        from src.app.modules.dashboard.schemas import TimeRange
        from src.app.modules.dashboard.service import dashboard_service

        db_mock = AsyncMock()
        start, end = await dashboard_service._get_date_range(db_mock, TimeRange.WEEK, "tenant")

        delta = end - start
        assert delta.days == 7

    @pytest.mark.asyncio
    async def test_date_range_month(self):
        """Test MONTH range returns ~30 days."""
        from src.app.modules.dashboard.schemas import TimeRange
        from src.app.modules.dashboard.service import dashboard_service

        db_mock = AsyncMock()
        start, end = await dashboard_service._get_date_range(db_mock, TimeRange.MONTH, "tenant")

        delta = end - start
        assert 29 <= delta.days <= 31

    @pytest.mark.asyncio
    async def test_date_range_quarter(self):
        """Test QUARTER range returns ~90 days."""
        from src.app.modules.dashboard.schemas import TimeRange
        from src.app.modules.dashboard.service import dashboard_service

        db_mock = AsyncMock()
        start, end = await dashboard_service._get_date_range(db_mock, TimeRange.QUARTER, "tenant")

        delta = end - start
        assert 89 <= delta.days <= 92

    @pytest.mark.asyncio
    async def test_date_range_year(self):
        """Test YEAR range returns ~365 days."""
        from src.app.modules.dashboard.schemas import TimeRange
        from src.app.modules.dashboard.service import dashboard_service

        db_mock = AsyncMock()
        start, end = await dashboard_service._get_date_range(db_mock, TimeRange.YEAR, "tenant")

        delta = end - start
        assert 364 <= delta.days <= 366

    @pytest.mark.asyncio
    async def test_date_range_all_with_min_date(self):
        """Test ALL range uses database min date when available."""

        from src.app.modules.dashboard.schemas import TimeRange
        from src.app.modules.dashboard.service import dashboard_service

        db_mock = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar.return_value = datetime(2023, 1, 1, tzinfo=UTC)
        db_mock.execute = AsyncMock(return_value=mock_result)

        start, end = await dashboard_service._get_date_range(db_mock, TimeRange.ALL, "tenant")

        # Should use the min date from DB
        assert start.year == 2023
        assert start.month == 1
        assert start.day == 1

    @pytest.mark.asyncio
    async def test_date_range_all_fallback(self):
        """Test ALL range falls back to 365 days when no min date."""
        from src.app.modules.dashboard.schemas import TimeRange
        from src.app.modules.dashboard.service import dashboard_service

        db_mock = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar.return_value = None  # No min date
        db_mock.execute = AsyncMock(return_value=mock_result)

        start, end = await dashboard_service._get_date_range(db_mock, TimeRange.ALL, "tenant")

        delta = end - start
        assert delta.days == 365


class TestUserMetrics:
    """Test user metrics calculation."""

    @pytest.mark.asyncio
    async def test_get_user_metrics_returns_correct_structure(self):
        """Test user metrics returns proper UserMetrics object."""
        from src.app.modules.dashboard.service import dashboard_service

        db_mock = AsyncMock()
        mock_result = MagicMock()
        # Return tuple: (total, new, active, deleted)
        mock_result.one.return_value = (100, 10, 50, 5)
        db_mock.execute = AsyncMock(return_value=mock_result)

        start = datetime.now(UTC) - timedelta(days=30)
        end = datetime.now(UTC)

        result = await dashboard_service._get_user_metrics(db_mock, start, end, "tenant")

        assert result.total_users == 100
        assert result.new_users == 10
        assert result.active_users == 50
        assert result.deleted_users == 5

    @pytest.mark.asyncio
    async def test_get_user_metrics_with_zeros(self):
        """Test user metrics handles zero values."""
        from src.app.modules.dashboard.service import dashboard_service

        db_mock = AsyncMock()
        mock_result = MagicMock()
        mock_result.one.return_value = (0, 0, 0, 0)
        db_mock.execute = AsyncMock(return_value=mock_result)

        start = datetime.now(UTC) - timedelta(days=30)
        end = datetime.now(UTC)

        result = await dashboard_service._get_user_metrics(db_mock, start, end, "tenant")

        assert result.total_users == 0
        assert result.new_users == 0
        assert result.active_users == 0
        assert result.deleted_users == 0


class TestRevenueMetrics:
    """Test revenue metrics calculation."""

    @pytest.mark.asyncio
    async def test_get_revenue_metrics_with_data(self):
        """Test revenue metrics calculates correctly with billing data."""
        from src.app.modules.dashboard.service import dashboard_service

        db_mock = AsyncMock()
        mock_result = MagicMock()
        # Return tuple: (total_cents, subscription_count, mrr)
        mock_result.one.return_value = (100000, 50, 500.0)  # $1000, 50 subs, $500 MRR
        db_mock.execute = AsyncMock(return_value=mock_result)

        start = datetime.now(UTC) - timedelta(days=30)
        end = datetime.now(UTC)

        result = await dashboard_service._get_revenue_metrics(db_mock, start, end, "tenant")

        assert result.total_revenue == 1000.0  # cents / 100
        assert result.total_subscriptions == 50
        assert result.recurring_revenue == 500.0

    @pytest.mark.asyncio
    async def test_get_revenue_metrics_zero_subs(self):
        """Test revenue metrics handles zero subscriptions (avoids division by zero)."""
        from src.app.modules.dashboard.service import dashboard_service

        db_mock = AsyncMock()
        mock_result = MagicMock()
        mock_result.one.return_value = (0, 0, 0.0)  # No revenue, no subs, no MRR
        db_mock.execute = AsyncMock(return_value=mock_result)

        start = datetime.now(UTC) - timedelta(days=30)
        end = datetime.now(UTC)

        result = await dashboard_service._get_revenue_metrics(db_mock, start, end, "tenant")

        assert result.total_revenue == 0.0
        assert result.total_subscriptions == 0
        assert result.average_revenue_per_user == 0.0
        assert result.recurring_revenue == 0.0


class TestActivityMetrics:
    """Test activity metrics calculation."""

    @pytest.mark.asyncio
    async def test_get_activity_metrics_returns_correct_structure(self):
        """Test activity metrics returns proper ActivityMetrics object."""
        from src.app.modules.dashboard.service import dashboard_service

        db_mock = AsyncMock()
        mock_result = MagicMock()
        # Return tuple: (logins, api_calls, failed_auth, webhooks)
        mock_result.one.return_value = (100, 5000, 10, 25)
        db_mock.execute = AsyncMock(return_value=mock_result)

        start = datetime.now(UTC) - timedelta(days=30)
        end = datetime.now(UTC)

        result = await dashboard_service._get_activity_metrics(db_mock, start, end, "tenant")

        assert result.total_logins == 100
        assert result.api_calls == 5000
        assert result.failed_authentications == 10
        assert result.webhook_deliveries == 25

    @pytest.mark.asyncio
    async def test_get_activity_metrics_with_zeros(self):
        """Test activity metrics handles zero values."""
        from src.app.modules.dashboard.service import dashboard_service

        db_mock = AsyncMock()
        mock_result = MagicMock()
        mock_result.one.return_value = (0, 0, 0, 0)
        db_mock.execute = AsyncMock(return_value=mock_result)

        start = datetime.now(UTC) - timedelta(days=30)
        end = datetime.now(UTC)

        result = await dashboard_service._get_activity_metrics(db_mock, start, end, "tenant")

        assert result.total_logins == 0
        assert result.api_calls == 0
        assert result.failed_authentications == 0
        assert result.webhook_deliveries == 0


class TestGetMetrics:
    """Test full get_metrics flow."""

    @pytest.mark.asyncio
    async def test_get_metrics_returns_dashboard_response(self):
        """Test get_metrics returns complete DashboardMetricsResponse."""
        from src.app.modules.dashboard.schemas import (
            ActivityMetrics,
            RevenueMetrics,
            TimeRange,
            UserMetrics,
        )
        from src.app.modules.dashboard.service import dashboard_service

        db_mock = AsyncMock()

        # Mock _get_date_range
        start = datetime.now(UTC) - timedelta(days=30)
        end = datetime.now(UTC)

        with patch.object(dashboard_service, "_get_date_range", return_value=(start, end)):
            # Use AsyncMock for the metrics methods to properly await
            with patch.object(dashboard_service, "_get_user_metrics", new_callable=AsyncMock) as user_mock:
                with patch.object(dashboard_service, "_get_revenue_metrics", new_callable=AsyncMock) as rev_mock:
                    with patch.object(dashboard_service, "_get_activity_metrics", new_callable=AsyncMock) as act_mock:
                        # Set return values for the async mocks
                        user_mock.return_value = UserMetrics(
                            total_users=100, new_users=10, active_users=50, deleted_users=2
                        )
                        rev_mock.return_value = RevenueMetrics(
                            total_revenue=1000.0,
                            recurring_revenue=800.0,
                            average_revenue_per_user=10.0,
                            total_subscriptions=50,
                        )
                        act_mock.return_value = ActivityMetrics(
                            total_logins=500,
                            api_calls=10000,
                            webhook_deliveries=100,
                            failed_authentications=5,
                        )

                        result = await dashboard_service.get_metrics(db_mock, "tenant", TimeRange.MONTH)

                        assert result.time_range == TimeRange.MONTH
                        assert result.start_date == start
                        assert result.end_date == end
                        user_mock.assert_called_once()
                        rev_mock.assert_called_once()
                        act_mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_metrics_uses_default_time_range(self):
        """Test get_metrics defaults to MONTH time range."""
        from src.app.modules.dashboard.schemas import (
            ActivityMetrics,
            RevenueMetrics,
            TimeRange,
            UserMetrics,
        )
        from src.app.modules.dashboard.service import dashboard_service

        db_mock = AsyncMock()

        with patch.object(dashboard_service, "_get_date_range", return_value=(datetime.now(UTC), datetime.now(UTC))):
            with patch.object(dashboard_service, "_get_user_metrics", new_callable=AsyncMock) as user_mock:
                with patch.object(dashboard_service, "_get_revenue_metrics", new_callable=AsyncMock) as rev_mock:
                    with patch.object(dashboard_service, "_get_activity_metrics", new_callable=AsyncMock) as act_mock:
                        user_mock.return_value = UserMetrics(
                            total_users=100, new_users=10, active_users=50, deleted_users=2
                        )
                        rev_mock.return_value = RevenueMetrics(
                            total_revenue=1000.0,
                            recurring_revenue=800.0,
                            average_revenue_per_user=10.0,
                            total_subscriptions=50,
                        )
                        act_mock.return_value = ActivityMetrics(
                            total_logins=500,
                            api_calls=10000,
                            webhook_deliveries=100,
                            failed_authentications=5,
                        )

                        # Call without time_range - should default to MONTH
                        result = await dashboard_service.get_metrics(db_mock, "tenant")

                        assert result.time_range == TimeRange.MONTH


class TestDashboardMetricsResponse:
    """Test DashboardMetricsResponse schema."""

    def test_response_includes_all_metric_types(self):
        """Test response contains user, revenue, and activity metrics."""
        from src.app.modules.dashboard.schemas import (
            ActivityMetrics,
            DashboardMetricsResponse,
            RevenueMetrics,
            TimeRange,
            UserMetrics,
        )

        now = datetime.now(UTC)

        response = DashboardMetricsResponse(
            time_range=TimeRange.MONTH,
            start_date=now - timedelta(days=30),
            end_date=now,
            users=UserMetrics(total_users=100, new_users=10, active_users=50, deleted_users=2),
            revenue=RevenueMetrics(
                total_revenue=1000.0,
                recurring_revenue=800.0,
                average_revenue_per_user=10.0,
                total_subscriptions=50,
            ),
            activity=ActivityMetrics(
                total_logins=500,
                api_calls=10000,
                webhook_deliveries=100,
                failed_authentications=5,
            ),
        )

        assert isinstance(response.users, UserMetrics)
        assert isinstance(response.revenue, RevenueMetrics)
        assert isinstance(response.activity, ActivityMetrics)


class TestDashboardAuditVocabulary:
    """Fix #4: dashboard must query the audit action strings producers emit.

    Producers (verified, do not regress):
    - src/app/modules/auth/service.py::_publish_login_audit emits
      action="auth.login" with details={"success": bool, ...}
    - src/app/modules/auth/services/sso.py emits action="auth.sso_login"
    Zero producers emit bare "login" / "login_failed" / "authentication_failed".
    """

    PRODUCER_LOGIN_ACTIONS = {"auth.login", "auth.sso_login"}

    def _compiled_sql(self, stmt) -> str:
        """Compile a captured statement with literal binds for assertion."""
        from sqlalchemy.dialects import postgresql

        return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))

    @pytest.mark.asyncio
    async def test_user_metrics_queries_producer_login_actions(self):
        """Active-users query must intersect producer action strings."""
        from src.app.modules.dashboard.service import dashboard_service

        db_mock = AsyncMock()
        mock_result = MagicMock()
        mock_result.one.return_value = (100, 10, 50, 5)
        db_mock.execute = AsyncMock(return_value=mock_result)

        start = datetime.now(UTC) - timedelta(days=30)
        end = datetime.now(UTC)

        await dashboard_service._get_user_metrics(db_mock, start, end, "tenant")

        sql = self._compiled_sql(db_mock.execute.call_args[0][0])
        for action in self.PRODUCER_LOGIN_ACTIONS:
            assert action in sql
        assert "'login'" not in sql

    @pytest.mark.asyncio
    async def test_activity_metrics_queries_producer_login_actions(self):
        """Total-logins query must intersect producer strings; failed-auth
        must use auth.login + details success=false (never dead literals)."""
        from src.app.modules.dashboard.service import dashboard_service

        db_mock = AsyncMock()
        mock_result = MagicMock()
        mock_result.one.return_value = (100, 5000, 10, 25)
        db_mock.execute = AsyncMock(return_value=mock_result)

        start = datetime.now(UTC) - timedelta(days=30)
        end = datetime.now(UTC)

        await dashboard_service._get_activity_metrics(db_mock, start, end, "tenant")

        sql = self._compiled_sql(db_mock.execute.call_args[0][0])
        for action in self.PRODUCER_LOGIN_ACTIONS:
            assert action in sql
        assert "'login'" not in sql
        assert "login_failed" not in sql
        assert "authentication_failed" not in sql
        assert "success" in sql
        assert "'false'" in sql


class TestP111DashboardCache:
    """P1-11: get_metrics must serve cached aggregates and use a prefix
    LIKE for webhook actions (index-friendly, same match set)."""

    def _metrics(self):
        from src.app.modules.dashboard.schemas import (
            ActivityMetrics,
            RevenueMetrics,
            UserMetrics,
        )

        start = datetime.now(UTC) - timedelta(days=30)
        end = datetime.now(UTC)
        users = UserMetrics(total_users=100, active_users=80, new_users=10, deleted_users=2)
        revenue = RevenueMetrics(
            total_revenue=1000.0,
            recurring_revenue=800.0,
            average_revenue_per_user=12.5,
            total_subscriptions=50,
        )
        activity = ActivityMetrics(
            total_logins=500, api_calls=10000, webhook_deliveries=100, failed_authentications=5
        )
        return start, end, users, revenue, activity

    @pytest.mark.asyncio
    async def test_get_metrics_serves_cache_hit_without_db(self):
        """Cache hit returns validated response; no aggregate queries run."""
        from src.app.modules.dashboard.schemas import DashboardMetricsResponse, TimeRange
        from src.app.modules.dashboard.service import dashboard_service

        start, end, users, revenue, activity = self._metrics()
        cached = DashboardMetricsResponse(
            time_range=TimeRange.MONTH,
            start_date=start,
            end_date=end,
            users=users,
            revenue=revenue,
            activity=activity,
        )
        cache_mock = AsyncMock()
        cache_mock.get_json = AsyncMock(return_value=cached.model_dump(mode="json"))

        with (
            patch("src.app.modules.dashboard.service.cache_client", cache_mock),
            patch.object(dashboard_service, "_get_date_range", new_callable=AsyncMock) as date_mock,
        ):
            result = await dashboard_service.get_metrics(AsyncMock(), "tenant", TimeRange.MONTH)

        assert result == cached
        date_mock.assert_not_called()
        cache_mock.set_json.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_metrics_miss_computes_and_stores_with_ttl(self):
        """Cache miss computes aggregates and stores them for 60s."""
        from src.app.modules.dashboard.schemas import TimeRange
        from src.app.modules.dashboard.service import dashboard_service

        start, end, users, revenue, activity = self._metrics()
        cache_mock = AsyncMock()
        cache_mock.get_json = AsyncMock(return_value=None)
        # tenant_key is sync on the real adapter — keep it sync on the mock.
        cache_mock.tenant_key = MagicMock(side_effect=lambda key, tenant_id=None: f"{tenant_id}:{key}")

        with (
            patch("src.app.modules.dashboard.service.cache_client", cache_mock),
            patch.object(dashboard_service, "_get_date_range", return_value=(start, end)),
            patch.object(dashboard_service, "_get_user_metrics", return_value=users),
            patch.object(dashboard_service, "_get_revenue_metrics", return_value=revenue),
            patch.object(dashboard_service, "_get_activity_metrics", return_value=activity),
        ):
            result = await dashboard_service.get_metrics(AsyncMock(), "tenant-1", TimeRange.MONTH)

        assert result.users == users
        assert result.revenue == revenue
        assert result.activity == activity
        cache_mock.set_json.assert_awaited_once()
        key, payload = cache_mock.set_json.call_args[0][:2]
        kwargs = cache_mock.set_json.call_args[1]
        assert "tenant-1" in key
        assert "month" in key
        assert kwargs.get("ttl") == 60
        assert payload["users"]["total_users"] == 100

    @pytest.mark.asyncio
    async def test_webhook_subquery_uses_prefix_like(self):
        """Webhook count must use prefix LIKE (btree-friendly), never %..%."""
        from sqlalchemy.dialects import postgresql

        from src.app.modules.dashboard.service import dashboard_service

        db_mock = AsyncMock()
        mock_result = MagicMock()
        mock_result.one.return_value = (100, 5000, 10, 25)
        db_mock.execute = AsyncMock(return_value=mock_result)

        start = datetime.now(UTC) - timedelta(days=30)
        end = datetime.now(UTC)
        await dashboard_service._get_activity_metrics(db_mock, start, end, "tenant")

        sql = str(
            db_mock.execute.call_args[0][0].compile(
                dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
            )
        )
        assert "webhook.%" in sql
        assert "%webhook%" not in sql


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
