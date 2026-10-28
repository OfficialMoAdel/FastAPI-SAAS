"""Unit tests for the query profiler module.

Tests cover:
- Query normalization (N+1 detection)
- Query tracking (global and per-request)
- N+1 pattern detection
- Request tracking lifecycle
- Environment-based profiler enablement
"""

import pytest
from sqlalchemy import create_engine

from src.app.core.db.profiler import (
    get_query_stats,
    is_profiler_enabled,
    normalize_query_for_pattern,
    reset_query_tracker,
    track_global_query,
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
    yield
    reset_query_tracker()


class TestQueryNormalization:
    """Tests for SQL query normalization."""

    def test_normalize_query_basic(self):
        """Test basic query normalization with string literals."""

        query = "SELECT * FROM users WHERE name = 'John' AND age = 25"
        result = normalize_query_for_pattern(query)

        assert "'?'" in result
        assert "'John'" not in result
        assert "25" not in result

    def test_normalize_query_numbers(self):
        """Test normalization of numeric values."""

        query = "SELECT * FROM posts WHERE id = 123 AND status = 1"
        result = normalize_query_for_pattern(query)

        # Numbers should be replaced with ?
        assert "123" not in result or result.count("?") >= 2

    def test_normalize_query_complex(self):
        """Test normalization of complex query."""

        query = """
            SELECT u.name, p.title
            FROM users u
            JOIN posts p ON u.id = p.user_id
            WHERE u.status = 'active' AND p.created_at > '2024-01-01'
        """
        result = normalize_query_for_pattern(query)

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


class TestCrossRequestTracking:
    """The entry point the single engine listener actually calls.

    `setup_query_profiler` used to live here and registered a SECOND
    before/after cursor-execute pair on the same engine as
    `QueryProfilerMiddleware`, behind the same `QUERY_PROFILER_ENABLED` flag.
    That double instrumentation is finding `muse 12`. The engine listener now
    lives in one place and calls `track_global_query`, so this module keeps
    the cross-request view without owning any listener.
    """

    def test_track_global_query_records_and_counts(self):
        reset_query_tracker()
        try:
            for _ in range(3):
                track_global_query("SELECT * FROM users WHERE id = 7")

            stats = get_query_stats()
            assert len(stats) == 1, f"expected one normalised pattern, got {list(stats)}"
            assert next(iter(stats.values()))["count"] == 3
        finally:
            reset_query_tracker()

    def test_track_global_query_normalises_literals(self):
        """Different literal values are ONE pattern, not N.

        This is the whole point of tracking across requests: the recurring
        shape is the signal, and the bind values are noise.
        """
        reset_query_tracker()
        try:
            track_global_query("SELECT * FROM users WHERE id = 1")
            track_global_query("SELECT * FROM users WHERE id = 2")

            stats = get_query_stats()
            assert len(stats) == 1, f"literals were not normalised: {list(stats)}"
            assert next(iter(stats.values()))["count"] == 2
        finally:
            reset_query_tracker()


class TestIntegration:
    """Cross-request tracker: the surface that survived the deletion.

    The per-request tracker these tests used to exercise was deleted (nothing
    started it; the middleware owns per-request counting). What remains, and
    what matters operationally, is the CROSS-request view: a pattern no single
    request repeats but that recurs across the process.
    """

    def test_a_recurring_pattern_is_counted_across_calls(self):
        """The tracker accumulates across separate "requests"."""
        for _ in range(4):
            track_global_query("SELECT * FROM users WHERE id = 1")

        stats = get_query_stats()
        assert len(stats) == 1
        assert next(iter(stats.values()))["count"] == 4

    def test_distinct_literals_are_the_same_pattern(self):
        """This is the capability that was rescued from the deleted tracker.

        ``id = 1`` and ``id = 2`` are one N+1 pattern, not two queries. The
        middleware's per-request counts use the SAME normaliser, so both
        detectors agree on what "the same query" means.
        """
        track_global_query("SELECT * FROM users WHERE id = 1")
        track_global_query("SELECT * FROM users WHERE id = 2")
        track_global_query("SELECT * FROM users WHERE name = 'bob'")

        stats = get_query_stats()
        assert len(stats) == 2, f"expected two patterns, got {sorted(stats)}"
        assert "?" in " ".join(stats), "literals must be collapsed to a placeholder"


def test_empty_stats_after_reset():
    """Verify stats are empty after reset."""
    from src.app.core.db.profiler import _query_tracker

    _query_tracker["Some query"] = {"count": 1, "first_seen_at": "2024-01-01T00:00:00"}
    reset_query_tracker()

    assert len(get_query_stats()) == 0


class TestTheLivePerRequestDetectorCollapsesLiterals:
    """The capability that was rescued before the dead tracker was deleted.

    ``core/db/profiler`` used to hold a SECOND per-request tracker whose only
    distinguishing feature was ``_normalize_query``: it collapses string and
    numeric literals, so ``WHERE id = 1`` and ``WHERE id = 2`` are one N+1
    pattern. The tracker had no production caller, but the LIVE detector in
    ``middleware/query_profiler.py`` bucketed on whitespace alone — so an N+1
    with varying values counted as N distinct queries and was never reported.

    Deleting the dead tracker without first moving the normaliser would have
    removed the capability instead of the code. These tests pin the merge.
    """

    def test_varying_values_are_one_pattern(self) -> None:
        from src.app.middleware.query_profiler import QueryStats

        stats = QueryStats()
        for user_id in (1, 2, 3, 4):
            stats.add_query(f"SELECT * FROM users WHERE id = {user_id}", {}, 1.0)

        assert len(stats.query_counts) == 1, (
            f"an N+1 with varying values must be ONE pattern, got "
            f"{list(stats.query_counts)}"
        )
        assert next(iter(stats.query_counts.values())) == 4

    def test_string_literals_collapse_too(self) -> None:
        from src.app.middleware.query_profiler import QueryStats

        stats = QueryStats()
        stats.add_query("SELECT * FROM t WHERE name = 'alice'", {}, 1.0)
        stats.add_query("SELECT * FROM t WHERE name = 'bob'", {}, 1.0)

        assert len(stats.query_counts) == 1
        assert "'?'" in next(iter(stats.query_counts))

    def test_genuinely_different_queries_stay_separate(self) -> None:
        """The normaliser must not collapse across different shapes."""
        from src.app.middleware.query_profiler import QueryStats

        stats = QueryStats()
        stats.add_query("SELECT * FROM users WHERE id = 1", {}, 1.0)
        stats.add_query("SELECT * FROM posts WHERE id = 1", {}, 1.0)

        assert len(stats.query_counts) == 2

    def test_the_detail_field_still_shows_the_real_statement(self) -> None:
        """A reader debugging a slow query needs the literal, not the pattern."""
        from src.app.middleware.query_profiler import QueryStats

        stats = QueryStats()
        stats.add_query("SELECT * FROM users WHERE id = 42", {}, 1.0)

        assert "42" in stats.queries[0]["normalized"], (
            "the human-facing detail must keep the bound value"
        )
