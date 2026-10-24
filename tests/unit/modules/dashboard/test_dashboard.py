"""
Dashboard Tests

Unit tests for the dashboard module.
"""

import time
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
            # Merged path: one statement for all three groups.
            with patch.object(dashboard_service, "_get_all_metrics", new_callable=AsyncMock) as all_mock:
                # Set return values for the async mock
                all_mock.return_value = (
                    UserMetrics(total_users=100, new_users=10, active_users=50, deleted_users=2),
                    RevenueMetrics(
                        total_revenue=1000.0,
                        recurring_revenue=800.0,
                        average_revenue_per_user=10.0,
                        total_subscriptions=50,
                    ),
                    ActivityMetrics(
                        total_logins=500,
                        api_calls=10000,
                        webhook_deliveries=100,
                        failed_authentications=5,
                    ),
                    None,
                )

                result = await dashboard_service.get_metrics(db_mock, "tenant", TimeRange.MONTH)

                assert result.time_range == TimeRange.MONTH
                assert result.start_date == start
                assert result.end_date == end
                all_mock.assert_called_once()
                assert db_mock.execute.call_count == 0

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
            with patch.object(dashboard_service, "_get_all_metrics", new_callable=AsyncMock) as all_mock:
                all_mock.return_value = (
                    UserMetrics(total_users=100, new_users=10, active_users=50, deleted_users=2),
                    RevenueMetrics(
                        total_revenue=1000.0,
                        recurring_revenue=800.0,
                        average_revenue_per_user=10.0,
                        total_subscriptions=50,
                    ),
                    ActivityMetrics(
                        total_logins=500,
                        api_calls=10000,
                        webhook_deliveries=100,
                        failed_authentications=5,
                    ),
                    None,
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
            patch.object(
                dashboard_service,
                "_get_all_metrics",
                return_value=(users, revenue, activity, None),
            ),
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
    async def test_p2_4_concurrent_misses_compute_once(self):
        """P2-4: a herd on a cold key must not stampede the aggregates.

        Two concurrent callers miss the cache. Exactly one claims the
        refresh lock and computes; the other waits on the key, is served
        the stored payload, and never runs ``_get_all_metrics``. The
        published result is the same object for both callers.
        """
        import asyncio

        from src.app.modules.dashboard.schemas import TimeRange
        from src.app.modules.dashboard.service import dashboard_service

        start, end, users, revenue, activity = self._metrics()
        store: dict[str, dict] = {}
        gets = 0

        class FakeCache:
            def tenant_key(self, key, tenant_id=None):
                return f"{tenant_id}:{key}"

            async def get_json(self, key):
                nonlocal gets
                gets += 1
                # The waiter's poll must observe the winner's write.
                return store.get(key)

            async def set_json(self, key, value, ttl=None):
                store[key] = value

            async def set_nx(self, key, value, ttl=None):
                if key in store:
                    return False
                store[key] = True
                return True

            @property
            def client(self):
                return object()  # Redis is present in this scenario

            async def release_lock_if_owned(self, key, token):
                return True

        fake = FakeCache()

        async def _slow_compute(*args, **kwargs):
            await asyncio.sleep(0.02)
            return users, revenue, activity, None

        with (
            patch("src.app.modules.dashboard.service.cache_client", fake),
            patch.object(dashboard_service, "_get_date_range", return_value=(start, end)),
            patch.object(dashboard_service, "_get_all_metrics", side_effect=_slow_compute),
        ):
            results = await asyncio.gather(
                dashboard_service.get_metrics(AsyncMock(), "tenant-1", TimeRange.MONTH),
                dashboard_service.get_metrics(AsyncMock(), "tenant-1", TimeRange.MONTH),
            )

        # The aggregates ran exactly once: the lock holder computed, the
        # waiter read the stored payload.
        assert results[0].users == results[1].users == users
        assert results[0] == results[1]
        assert fake.tenant_key("dashboard:metrics:month", "tenant-1") in store

    @pytest.mark.asyncio
    async def test_p2_4_lock_holder_computes_and_releases(self):
        """P2-4: the lock holder always releases, and only its own compute
        path runs (no double-check leak when the winner wrote first)."""
        from src.app.modules.dashboard.schemas import TimeRange
        from src.app.modules.dashboard.service import dashboard_service

        start, end, users, revenue, activity = self._metrics()
        cache_mock = AsyncMock()
        cache_mock.get_json = AsyncMock(return_value=None)
        cache_mock.tenant_key = MagicMock(side_effect=lambda key, tenant_id=None: f"{tenant_id}:{key}")
        cache_mock.set_nx = AsyncMock(return_value=True)
        cache_mock.release_lock_if_owned = AsyncMock(return_value=True)

        with (
            patch("src.app.modules.dashboard.service.cache_client", cache_mock),
            patch.object(dashboard_service, "_get_date_range", return_value=(start, end)),
            patch.object(
                dashboard_service,
                "_get_all_metrics",
                return_value=(users, revenue, activity, None),
            ),
        ):
            result = await dashboard_service.get_metrics(AsyncMock(), "tenant-1", TimeRange.MONTH)

        assert result.users == users
        cache_mock.set_nx.assert_awaited_once()
        cache_mock.release_lock_if_owned.assert_awaited_once()
        assert cache_mock.set_json.await_count == 1

    @pytest.mark.asyncio
    async def test_p1_2_a_raising_set_nx_still_computes(self) -> None:
        """P1-2: a Redis error on the claim must not fail the dashboard.

        ``set_nx`` is the adapter's one non-fail-open operation: it
        propagates so the billing webhook can fall back to its DB
        idempotency check. The dashboard has no such fallback, so letting the
        error escape turned a transient Redis blip into a 500 where the
        pre-P2-4 code simply recomputed. The claim is best-effort here.
        """
        from src.app.modules.dashboard.schemas import TimeRange
        from src.app.modules.dashboard.service import dashboard_service

        start, end, users, revenue, activity = self._metrics()
        cache_mock = AsyncMock()
        cache_mock.get_json = AsyncMock(return_value=None)
        cache_mock.tenant_key = MagicMock(side_effect=lambda key, tenant_id=None: f"{tenant_id}:{key}")
        cache_mock.client = MagicMock()  # Redis is present
        cache_mock.set_nx = AsyncMock(side_effect=ConnectionError("redis is down"))

        with (
            patch("src.app.modules.dashboard.service.cache_client", cache_mock),
            patch.object(dashboard_service, "_get_date_range", return_value=(start, end)),
            patch.object(
                dashboard_service,
                "_get_all_metrics",
                return_value=(users, revenue, activity, None),
            ) as compute,
        ):
            result = await dashboard_service.get_metrics(AsyncMock(), "tenant-1", TimeRange.MONTH)

        # Recomputed and served -- the request did not fail.
        assert result.users == users
        compute.assert_awaited_once()
        cache_mock.set_json.assert_awaited_once()
        # Not a lock holder, so no release is attempted.
        cache_mock.release_lock_if_owned.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_p1_2_absent_client_computes_without_waiting(self) -> None:
        """P1-2: no Redis client means "cannot claim", not "lock held".

        ``set_nx`` returns False when the client is None, which is
        indistinguishable from a peer holding the lock. Every concurrent
        caller then polled for the full 5 s wait budget before computing —
        measured 5.02 s per request under a Redis outage. The client check
        happens before the claim, so an absent client goes straight to
        computing.
        """
        from src.app.modules.dashboard.schemas import TimeRange
        from src.app.modules.dashboard.service import dashboard_service

        start, end, users, revenue, activity = self._metrics()
        cache_mock = AsyncMock()
        cache_mock.get_json = AsyncMock(return_value=None)
        cache_mock.tenant_key = MagicMock(side_effect=lambda key, tenant_id=None: f"{tenant_id}:{key}")
        cache_mock.client = None
        cache_mock.set_nx = AsyncMock(return_value=False)

        with (
            patch("src.app.modules.dashboard.service.cache_client", cache_mock),
            patch.object(dashboard_service, "_get_date_range", return_value=(start, end)),
            patch.object(
                dashboard_service,
                "_get_all_metrics",
                return_value=(users, revenue, activity, None),
            ),
        ):
            started = time.monotonic()
            result = await dashboard_service.get_metrics(AsyncMock(), "tenant-1", TimeRange.MONTH)
            elapsed = time.monotonic() - started

        assert result.users == users
        # The claim is never attempted against an absent client.
        cache_mock.set_nx.assert_not_awaited()
        cache_mock.release_lock_if_owned.assert_not_awaited()
        # And it certainly did not burn the 5 s wait budget.
        assert elapsed < 1.0, f"absent-client path waited {elapsed:.2f}s"

    @pytest.mark.parametrize("error", [ConnectionError("conn refused"), OSError("socket gone")])
    async def test_p1_2_a_transport_level_claim_failure_still_computes(self, error) -> None:
        """P1-2 (revision): the transport class is not a RedisError.

        ``set_nx`` propagates whatever ``client.set()`` raises. A Redis outage
        surfaces as ``redis.exceptions.ConnectionError`` (a ``RedisError``),
        but a replaced client or a socket that dies mid-call surfaces as the
        BUILTIN ``ConnectionError``, which is an ``OSError`` and NOT a
        ``RedisError``. Narrowing the guard to ``RedisError`` alone therefore
        let the exact failure it was written for — "redis is down" — escape
        and 500 the dashboard.
        """
        from src.app.modules.dashboard.schemas import TimeRange
        from src.app.modules.dashboard.service import dashboard_service

        start, end, users, revenue, activity = self._metrics()
        cache_mock = AsyncMock()
        cache_mock.get_json = AsyncMock(return_value=None)
        cache_mock.tenant_key = MagicMock(side_effect=lambda key, tenant_id=None: f"{tenant_id}:{key}")
        cache_mock.client = MagicMock()  # Redis is present
        cache_mock.set_nx = AsyncMock(side_effect=error)

        with (
            patch("src.app.modules.dashboard.service.cache_client", cache_mock),
            patch.object(dashboard_service, "_get_date_range", return_value=(start, end)),
            patch.object(
                dashboard_service,
                "_get_all_metrics",
                return_value=(users, revenue, activity, None),
            ),
        ):
            result = await dashboard_service.get_metrics(AsyncMock(), "tenant-1", TimeRange.MONTH)

        assert result.users == users
        cache_mock.release_lock_if_owned.assert_not_awaited()

    async def test_p1_2_a_programming_error_is_not_swallowed(self) -> None:
        """The guard covers transport failures, not bugs.

        A ``TypeError`` from the claim means the call itself is wrong (bad key
        type, wrong arity). Swallowing it would silently degrade every
        dashboard load to a recompute while looking healthy.
        """
        from src.app.modules.dashboard.schemas import TimeRange
        from src.app.modules.dashboard.service import dashboard_service

        start, end, users, revenue, activity = self._metrics()
        cache_mock = AsyncMock()
        cache_mock.get_json = AsyncMock(return_value=None)
        cache_mock.tenant_key = MagicMock(side_effect=lambda key, tenant_id=None: f"{tenant_id}:{key}")
        cache_mock.client = MagicMock()
        cache_mock.set_nx = AsyncMock(side_effect=TypeError("bad key type"))

        with (
            patch("src.app.modules.dashboard.service.cache_client", cache_mock),
            patch.object(dashboard_service, "_get_date_range", return_value=(start, end)),
            patch.object(
                dashboard_service,
                "_get_all_metrics",
                return_value=(users, revenue, activity, None),
            ),
        ):
            with pytest.raises(TypeError):
                await dashboard_service.get_metrics(AsyncMock(), "tenant-1", TimeRange.MONTH)


class TestGetAllMetrics:
    """P2: _get_all_metrics fetches all 11 aggregates in ONE statement."""

    def _row(self, min_created=None):
        row = (100, 10, 50, 2, 100000, 50, 800.0, 500, 10000, 5, 100)
        return row + ((min_created,) if min_created is not None else ())

    def _db(self, row):
        db_mock = AsyncMock()
        mock_result = MagicMock()
        mock_result.one.return_value = row
        db_mock.execute = AsyncMock(return_value=mock_result)
        return db_mock

    @pytest.mark.asyncio
    async def test_single_execute_eleven_column_layout(self):
        """One execute, 11 selected columns, groups assembled at fixed offsets."""
        from src.app.modules.dashboard.service import dashboard_service

        start = datetime.now(UTC) - timedelta(days=30)
        end = datetime.now(UTC)
        db_mock = self._db(self._row())

        users, revenue, activity, min_created = await dashboard_service._get_all_metrics(
            db_mock, start, end, "tenant"
        )

        db_mock.execute.assert_awaited_once()
        stmt = db_mock.execute.call_args[0][0]
        assert len(stmt.selected_columns) == 11
        assert (users.total_users, users.new_users, users.active_users, users.deleted_users) == (100, 10, 50, 2)
        assert (revenue.total_revenue, revenue.recurring_revenue, revenue.total_subscriptions) == (1000.0, 800.0, 50)
        assert revenue.average_revenue_per_user == 20.0
        assert (activity.total_logins, activity.api_calls) == (500, 10000)
        assert (activity.failed_authentications, activity.webhook_deliveries) == (5, 100)
        assert min_created is None

    @pytest.mark.asyncio
    async def test_include_min_created_appends_twelfth_column(self):
        """include_min_created adds the min column at index 11 and returns it."""
        from src.app.modules.dashboard.service import dashboard_service

        start = datetime.now(UTC) - timedelta(days=30)
        end = datetime.now(UTC)
        earliest = datetime.now(UTC) - timedelta(days=200)
        db_mock = self._db(self._row(min_created=earliest))

        users, revenue, activity, min_created = await dashboard_service._get_all_metrics(
            db_mock, start, end, "tenant", include_min_created=True
        )

        db_mock.execute.assert_awaited_once()
        stmt = db_mock.execute.call_args[0][0]
        assert len(stmt.selected_columns) == 12
        assert min_created == earliest
        assert users.total_users == 100
        assert activity.webhook_deliveries == 100

    @pytest.mark.asyncio
    async def test_get_metrics_month_single_execute(self):
        """MONTH path: date range (0 RTT) + one merged statement."""
        from src.app.modules.dashboard.schemas import TimeRange
        from src.app.modules.dashboard.service import dashboard_service

        start = datetime.now(UTC) - timedelta(days=30)
        end = datetime.now(UTC)
        db_mock = self._db(self._row())
        cache_mock = AsyncMock()
        cache_mock.get_json = AsyncMock(return_value=None)
        cache_mock.tenant_key = MagicMock(side_effect=lambda key, tenant_id=None: f"{tenant_id}:{key}")

        with (
            patch("src.app.modules.dashboard.service.cache_client", cache_mock),
            patch.object(dashboard_service, "_get_date_range", return_value=(start, end)) as date_mock,
        ):
            result = await dashboard_service.get_metrics(db_mock, "tenant-1", TimeRange.MONTH)

        date_mock.assert_awaited_once()
        db_mock.execute.assert_awaited_once()
        assert result.users.total_users == 100
        assert result.revenue.total_revenue == 1000.0
        assert result.activity.api_calls == 10000
        cache_mock.set_json.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_metrics_all_folds_min_into_start(self):
        """ALL path: no separate min query; start_date comes from the merged row."""
        from src.app.modules.dashboard.schemas import TimeRange
        from src.app.modules.dashboard.service import dashboard_service

        earliest = datetime.now(UTC) - timedelta(days=200)
        db_mock = self._db(self._row(min_created=earliest))
        cache_mock = AsyncMock()
        cache_mock.get_json = AsyncMock(return_value=None)
        cache_mock.tenant_key = MagicMock(side_effect=lambda key, tenant_id=None: f"{tenant_id}:{key}")

        with (
            patch("src.app.modules.dashboard.service.cache_client", cache_mock),
            patch.object(dashboard_service, "_get_date_range", new_callable=AsyncMock) as date_mock,
        ):
            result = await dashboard_service.get_metrics(db_mock, "tenant-1", TimeRange.ALL)

        date_mock.assert_not_called()
        db_mock.execute.assert_awaited_once()
        assert result.start_date == earliest
        assert result.users.total_users == 100


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
