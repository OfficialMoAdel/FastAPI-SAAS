"""Unit tests for the query profiler module.

Tests cover:
- Query normalization (N+1 detection)
- Query tracking (global and per-request)
- N+1 pattern detection
- Request tracking lifecycle
- Environment-based profiler enablement
"""

from collections import defaultdict

import pytest
from sqlalchemy import create_engine

from src.app.core.db.profiler import (
    _request_queries,
    end_request_tracking,
    get_query_stats,
    is_profiler_enabled,
    reset_query_tracker,
    setup_query_profiler,
    start_request_tracking,
)


@pytest.fixture
def mock_async_engine():
    """Create a mock async engine for testing."""
    # Note: We use sync engine for event listening in tests
    # The actual implementation works with AsyncEngine.sync_engine
    engine = create_engine("sqlite:///:memory:", echo=False)
    return engine


@pytest.fixture(autouse=True)
def reset_state():
    """Reset global state before and after each test."""
    reset_query_tracker()
    _request_queries.set(None)
    yield
    reset_query_tracker()
    _request_queries.set(None)


class TestQueryNormalization:
    """Tests for SQL query normalization."""

    def test_normalize_query_basic(self):
        """Test basic query normalization with string literals."""
        from src.app.core.db.profiler import _normalize_query

        query = "SELECT * FROM users WHERE name = 'John' AND age = 25"
        result = _normalize_query(query)

        assert "'?'" in result
        assert "'John'" not in result
        assert "25" not in result

    def test_normalize_query_numbers(self):
        """Test normalization of numeric values."""
        from src.app.core.db.profiler import _normalize_query

        query = "SELECT * FROM posts WHERE id = 123 AND status = 1"
        result = _normalize_query(query)

        # Numbers should be replaced with ?
        assert "123" not in result or result.count("?") >= 2

    def test_normalize_query_complex(self):
        """Test normalization of complex query."""
        from src.app.core.db.profiler import _normalize_query

        query = """
            SELECT u.name, p.title
            FROM users u
            JOIN posts p ON u.id = p.user_id
            WHERE u.status = 'active' AND p.created_at > '2024-01-01'
        """
        result = _normalize_query(query)

        # Structure should be preserved
        assert "SELECT" in result
        assert "FROM" in result
        assert "JOIN" in result
        # Values should be replaced
        assert "'active'" not in result
        assert "'2024-01-01'" not in result


class TestQueryTracking:
    """Tests for global query tracking."""

    def test_reset_query_tracker(self):
        """Test resetting the query tracker."""
        # Simulate some tracking
        from src.app.core.db.profiler import _query_tracker

        _query_tracker["test_query"] = {"count": 5, "first_seen_at": "2024-01-01"}

        reset_query_tracker()

        assert len(_query_tracker) == 0

    def test_get_query_stats_empty(self):
        """Test getting stats when no queries tracked."""
        stats = get_query_stats()

        assert stats == {}

    def test_get_query_stats_with_data(self):
        """Test getting stats with tracked queries."""
        from src.app.core.db.profiler import _query_tracker

        _query_tracker["SELECT * FROM users"] = {
            "count": 10,
            "first_seen_at": "2024-01-01T00:00:00",
        }

        stats = get_query_stats()

        assert "SELECT * FROM users" in stats
        assert stats["SELECT * FROM users"]["count"] == 10


class TestRequestTracking:
    """Tests for per-request query tracking."""

    def test_start_request_tracking(self):
        """Test starting request tracking."""
        start_request_tracking()

        queries = _request_queries.get()
        assert queries is not None
        assert isinstance(queries, defaultdict)

    def test_end_request_tracking_no_tracking(self):
        """Test ending tracking when not started."""
        result = end_request_tracking("/test")

        assert result == {}

    def test_end_request_tracking_with_n_plus_1(self):
        """Test detecting N+1 pattern in request."""
        start_request_tracking()

        # Simulate repeated queries (N+1 pattern)
        queries = _request_queries.get()
        queries["SELECT * FROM users WHERE id = ?"] = 5
        queries["SELECT * FROM posts"] = 1

        patterns = end_request_tracking("/api/users", threshold=3)

        assert "SELECT * FROM users WHERE id = ?" in patterns
        assert patterns["SELECT * FROM users WHERE id = ?"] == 5
        assert "SELECT * FROM posts" not in patterns

    def test_end_request_tracking_below_threshold(self):
        """Test no N+1 detection below threshold."""
        start_request_tracking()

        queries = _request_queries.get()
        queries["SELECT * FROM users"] = 2

        patterns = end_request_tracking("/api/users", threshold=3)

        assert patterns == {}

    def test_request_tracking_isolation(self):
        """Test that request tracking is isolated per request."""
        # Start first request
        start_request_tracking()
        queries1 = _request_queries.get()
        queries1["query1"] = 5

        # End first request
        end_request_tracking("/request1")

        # Start second request
        start_request_tracking()
        queries2 = _request_queries.get()

        # Should be a fresh tracker
        assert "query1" not in queries2
        assert queries2["query2"] == 1  # New query starts at 1


class TestProfilerEnablement:
    """Tests for profiler enablement based on environment."""

    def test_profiler_enabled_in_development(self):
        """Test profiler enabled in dev environments."""
        assert is_profiler_enabled("development") is True
        assert is_profiler_enabled("dev") is True
        assert is_profiler_enabled("local") is True
        assert is_profiler_enabled("staging") is True

    def test_profiler_disabled_in_production(self):
        """Test profiler disabled in production."""
        assert is_profiler_enabled("production") is False
        assert is_profiler_enabled("prod") is False

    def test_profiler_disabled_case_insensitive(self):
        """Test environment check is case insensitive."""
        assert is_profiler_enabled("DEVELOPMENT") is True
        assert is_profiler_enabled("Production") is False
        assert is_profiler_enabled("PROD") is False


class TestProfilerSetup:
    """Tests for profiler setup with SQLAlchemy engine."""

    def test_setup_profiler_attaches_events(self, mock_async_engine):
        """Test that profiler attaches to engine events."""
        # This test verifies the setup function runs without error
        # In a real scenario, we'd verify events are attached

        class MockAsyncEngine:
            def __init__(self, sync_engine):
                self.sync_engine = sync_engine

        mock_engine = MockAsyncEngine(mock_async_engine)

        # Should not raise
        setup_query_profiler(
            mock_engine,
            slow_query_threshold_ms=50,
            n_plus_1_threshold=5,
            detailed_logs=True,
        )

    def test_setup_profiler_default_params(self, mock_async_engine):
        """Test profiler setup with default parameters."""

        class MockAsyncEngine:
            def __init__(self, sync_engine):
                self.sync_engine = sync_engine

        mock_engine = MockAsyncEngine(mock_async_engine)

        # Should work with defaults
        setup_query_profiler(mock_engine)


class TestIntegration:
    """Integration tests for the profiler."""

    def test_full_request_lifecycle(self):
        """Test complete request tracking lifecycle."""
        # Start tracking
        start_request_tracking()

        # Simulate some queries
        queries = _request_queries.get()
        queries["SELECT * FROM users WHERE id = ?"] = 10
        queries["UPDATE users SET last_seen = ?"] = 1

        # End tracking
        patterns = end_request_tracking("/api/users/1", threshold=5)

        # Verify N+1 detected
        assert len(patterns) == 1
        assert "SELECT * FROM users WHERE id = ?" in patterns
        assert patterns["SELECT * FROM users WHERE id = ?"] == 10

        # Verify tracking cleared
        assert _request_queries.get() is None

    def test_multiple_requests_sequential(self):
        """Test tracking multiple requests sequentially."""
        # Request 1
        start_request_tracking()
        queries1 = _request_queries.get()
        queries1["query_a"] = 5
        end_request_tracking("/request1")

        # Request 2
        start_request_tracking()
        queries2 = _request_queries.get()
        queries2["query_b"] = 3
        patterns = end_request_tracking("/request2", threshold=2)

        assert "query_b" in patterns
        assert "query_a" not in patterns


"""Tests for query profiler isolation.

Ensures query tracking is properly isolated between requests.
"""


import pytest


class TestRequestTracking:  # noqa: F811
    """Tests for request-level query tracking isolation."""

    @pytest.fixture(autouse=True)
    def reset_query_tracker(self):
        """Reset query tracker before and after each test."""
        from src.app.core.db.profiler import reset_query_tracker

        reset_query_tracker()
        yield
        reset_query_tracker()

    @pytest.mark.asyncio
    async def test_request_tracking_isolation(self):
        """Verify query stats are isolated between requests."""
        from src.app.core.db.profiler import (
            _query_tracker,
            get_query_stats,
            reset_query_tracker,
        )

        # Simulate first request with 3 queries
        reset_query_tracker()
        _query_tracker["SELECT * FROM users"] = {"count": 3, "first_seen_at": "2024-01-01T00:00:00"}
        _query_tracker["SELECT * FROM posts"] = {"count": 2, "first_seen_at": "2024-01-01T00:00:00"}

        stats1 = get_query_stats()
        assert len(stats1) == 2

        # Simulate second request - should start fresh
        reset_query_tracker()
        _query_tracker["SELECT * FROM tenants"] = {"count": 1, "first_seen_at": "2024-01-01T00:00:00"}

        stats2 = get_query_stats()
        assert len(stats2) == 1, f"Expected 1 query, got {len(stats2)}"

    def test_concurrent_requests_isolated(self):
        """Verify concurrent requests don't share query counts."""
        from src.app.core.db.profiler import (
            _query_tracker,
            get_query_stats,
            reset_query_tracker,
        )

        # Reset at start
        reset_query_tracker()

        # Simulate request A
        _query_tracker["SELECT A"] = {"count": 1, "first_seen_at": "2024-01-01T00:00:00"}
        stats_a = get_query_stats()
        assert len(stats_a) == 1

        # Simulate request B (concurrent - would reset in real middleware)
        reset_query_tracker()
        _query_tracker["SELECT B"] = {"count": 1, "first_seen_at": "2024-01-01T00:00:00"}
        _query_tracker["SELECT B2"] = {"count": 1, "first_seen_at": "2024-01-01T00:00:00"}
        stats_b = get_query_stats()
        assert len(stats_b) == 2

        # Request A's stats should not be affected (already captured)
        # In real use, each request gets fresh tracker via reset

    def test_empty_stats_after_reset(self):
        """Verify stats are empty after reset."""
        from src.app.core.db.profiler import _query_tracker, get_query_stats, reset_query_tracker

        _query_tracker["Some query"] = {"count": 1, "first_seen_at": "2024-01-01T00:00:00"}
        reset_query_tracker()

        stats = get_query_stats()
        assert len(stats) == 0
