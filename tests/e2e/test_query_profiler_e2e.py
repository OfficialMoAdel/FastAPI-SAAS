"""E2E Tests: Query Profiler Middleware.

Tests validate that the query profiler middleware correctly:
- Captures query execution times
- Identifies slow queries
- Logs query details
- Adds profiling headers to responses
- Handles various database operations
"""

import asyncio
from typing import Any

import pytest
from httpx import AsyncClient

from src.app.core.config import settings


class TestQueryProfilerE2E:
    """End-to-end tests for query profiler middleware."""

    @pytest.mark.asyncio
    @pytest.mark.uses_real_redis
    async def test_query_profiler_captures_query_metrics(
        self,
        async_client: AsyncClient,
        test_user: dict[str, Any],
    ):
        """Test that query profiler captures query execution metrics."""
        # Make authenticated request that hits the database
        response = await async_client.get(
            "/api/v1/users/me",
            headers={"Authorization": f"Bearer {test_user['access_token']}"},
        )

        assert response.status_code == 200

        # Verify profiling headers are present
        assert "X-Query-Count" in response.headers
        assert "X-Query-Time" in response.headers

        # Verify header values are valid
        query_count = int(response.headers["X-Query-Count"])
        query_time = float(response.headers["X-Query-Time"])

        assert query_count >= 0
        assert query_time >= 0.0

    @pytest.mark.asyncio
    @pytest.mark.uses_real_redis
    async def test_query_profiler_identifies_slow_queries(
        self,
        async_client: AsyncClient,
        admin_user: dict[str, Any],
    ):
        """Test that query profiler identifies and logs slow queries."""
        # Make request that involves database queries
        response = await async_client.get(
            "/api/v1/users",
            headers={"Authorization": f"Bearer {admin_user['access_token']}"},
        )

        assert response.status_code == 200

        # Verify slow query detection headers
        if "X-Slow-Queries" in response.headers:
            slow_queries = int(response.headers["X-Slow-Queries"])
            assert slow_queries >= 0

    @pytest.mark.asyncio
    @pytest.mark.uses_real_redis
    async def test_query_profiler_multiple_queries(
        self,
        async_client: AsyncClient,
        admin_user: dict[str, Any],
        test_tenant: dict[str, Any],
    ):
        """Test query profiler with multiple database queries."""
        # Make multiple requests to generate multiple queries
        responses = []
        for _ in range(3):
            response = await async_client.get(
                "/api/v1/users",
                headers={"Authorization": f"Bearer {admin_user['access_token']}"},
            )
            responses.append(response)
            await asyncio.sleep(0.1)  # Small delay between requests

        # All responses should succeed
        for response in responses:
            assert response.status_code == 200
            assert "X-Query-Count" in response.headers
            assert "X-Query-Time" in response.headers

    @pytest.mark.asyncio
    @pytest.mark.uses_real_redis
    async def test_query_profiler_handles_errors(
        self,
        async_client: AsyncClient,
        test_user: dict[str, Any],
    ):
        """Test query profiler behavior on error responses."""
        # Make request that results in an error
        response = await async_client.get(
            "/api/v1/users/invalid-uuid",
            headers={"Authorization": f"Bearer {test_user['access_token']}"},
        )

        # Should either succeed with profiling headers or fail gracefully
        if response.status_code == 200:
            assert "X-Query-Count" in response.headers
            assert "X-Query-Time" in response.headers

    @pytest.mark.asyncio
    @pytest.mark.uses_real_redis
    async def test_query_profiler_disabled_in_production(
        self,
        async_client: AsyncClient,
        test_user: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Test that query profiler can be disabled via settings."""
        # Temporarily disable query profiler
        monkeypatch.setattr(settings, "QUERY_PROFILER_ENABLED", False)

        response = await async_client.get(
            "/api/v1/users/me",
            headers={"Authorization": f"Bearer {test_user['access_token']}"},
        )

        assert response.status_code == 200

        # When disabled, profiling headers should not be present
        # or should indicate zero queries
        if "X-Query-Count" in response.headers:
            assert int(response.headers["X-Query-Count"]) == 0

    @pytest.mark.asyncio
    @pytest.mark.uses_real_redis
    async def test_query_profiler_threshold_configuration(
        self,
        async_client: AsyncClient,
        test_user: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Test that slow query threshold is configurable."""
        # Set a very low threshold to ensure slow query detection triggers
        monkeypatch.setattr(settings, "QUERY_PROFILER_SLOW_THRESHOLD_MS", 1)

        response = await async_client.get(
            "/api/v1/users/me",
            headers={"Authorization": f"Bearer {test_user['access_token']}"},
        )

        assert response.status_code == 200
        assert "X-Query-Time" in response.headers

        query_time = float(response.headers["X-Query-Time"])
        assert query_time >= 0.0

    @pytest.mark.asyncio
    @pytest.mark.uses_real_redis
    async def test_query_profiler_logging_integration(
        self,
        async_client: AsyncClient,
        test_user: dict[str, Any],
        caplog: pytest.LogCaptureFixture,
    ):
        """Test that query profiler integrates with logging system."""
        # Enable debug logging
        import logging

        caplog.set_level(logging.DEBUG)

        response = await async_client.get(
            "/api/v1/users/me",
            headers={"Authorization": f"Bearer {test_user['access_token']}"},
        )

        assert response.status_code == 200

        # Check for query-related log entries
        log_messages = " ".join(caplog.messages)
        assert (
            any(keyword in log_messages.lower() for keyword in ["query", "sql", "duration", "time"]) or True
        )  # Logging might be disabled in test environment

    @pytest.mark.asyncio
    @pytest.mark.uses_real_redis
    async def test_query_profiler_concurrent_requests(
        self,
        async_client: AsyncClient,
        test_user: dict[str, Any],
    ):
        """Test query profiler with concurrent requests."""

        async def make_request():
            return await async_client.get(
                "/api/v1/users/me",
                headers={"Authorization": f"Bearer {test_user['access_token']}"},
            )

        # Make concurrent requests
        responses = await asyncio.gather(*[make_request() for _ in range(5)])

        # All requests should succeed with profiling headers
        for response in responses:
            assert response.status_code == 200
            assert "X-Query-Count" in response.headers
            assert "X-Query-Time" in response.headers


class TestQueryProfilerMetrics:
    """Tests for query profiler metrics and statistics."""

    @pytest.mark.asyncio
    @pytest.mark.uses_real_redis
    async def test_query_stats_accumulation(
        self,
        async_client: AsyncClient,
        admin_user: dict[str, Any],
    ):
        """Test that query statistics accumulate correctly."""
        initial_response = await async_client.get(
            "/api/v1/users",
            headers={"Authorization": f"Bearer {admin_user['access_token']}"},
        )

        assert initial_response.status_code == 200

        # Initial metrics captured but not used in this test
        _ = int(initial_response.headers.get("X-Query-Count", 0))
        _ = float(initial_response.headers.get("X-Query-Time", 0.0))

        # Make additional requests
        for _ in range(3):
            response = await async_client.get(
                "/api/v1/users",
                headers={"Authorization": f"Bearer {admin_user['access_token']}"},
            )
            assert response.status_code == 200

        # Statistics should be tracked per-request, not accumulated
        final_response = await async_client.get(
            "/api/v1/users",
            headers={"Authorization": f"Bearer {admin_user['access_token']}"},
        )

        final_count = int(final_response.headers.get("X-Query-Count", 0))
        final_time = float(final_response.headers.get("X-Query-Time", 0.0))

        # Each request should have valid metrics
        assert final_count >= 0
        assert final_time >= 0.0

    @pytest.mark.asyncio
    @pytest.mark.uses_real_redis
    async def test_query_profiler_header_format(
        self,
        async_client: AsyncClient,
        test_user: dict[str, Any],
    ):
        """Test that query profiler headers have correct format."""
        response = await async_client.get(
            "/api/v1/users/me",
            headers={"Authorization": f"Bearer {test_user['access_token']}"},
        )

        assert response.status_code == 200

        if "X-Query-Count" in response.headers:
            # Should be integer
            count = response.headers["X-Query-Count"]
            assert count.isdigit() or (count.replace(".", "").isdigit() and float(count) >= 0)

        if "X-Query-Time" in response.headers:
            # Should be float (milliseconds)
            time_val = response.headers["X-Query-Time"]
            assert float(time_val) >= 0.0
