"""Query counting test utilities for counting database queries in tests.

Provides context managers and assertion helpers for measuring SQL query execution
during test operations. Uses SQLAlchemy event listeners to track queries.

Usage:
    from tests.fixtures.query_counter import count_queries, assert_query_count

    async def test_efficient_query():
        with count_queries() as counter:
            await get_users()

        assert counter.count == 1  # Should only query once
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


@dataclass
class QueryLog:
    """Log of SQL queries executed during a test."""

    count: int = 0
    statements: list[str] = field(default_factory=list)
    query_types: Counter = field(default_factory=Counter)

    def reset(self) -> None:
        """Reset the query log."""
        self.count = 0
        self.statements.clear()
        self.query_types.clear()

    def add_query(self, statement: str) -> None:
        """Add a query to the log."""
        self.count += 1
        self.statements.append(statement[:500])  # Truncate long queries
        self.query_types[self._get_query_type(statement)] += 1

    @staticmethod
    def _get_query_type(statement: str) -> str:
        """Extract the SQL query type (SELECT, INSERT, UPDATE, DELETE)."""
        match = re.match(r"^\s*(SELECT|INSERT|UPDATE|DELETE|BEGIN|COMMIT|ROLLBACK)", statement, re.IGNORECASE)
        return match.group(1).upper() if match else "OTHER"

    def get_summary(self) -> dict[str, Any]:
        """Get a summary of query statistics."""
        return {
            "total": self.count,
            "by_type": dict(self.query_types),
        }

    def __repr__(self) -> str:
        return f"QueryLog(count={self.count}, types={dict(self.query_types)})"


_query_log = QueryLog()


@event.listens_for(Engine, "before_cursor_execute")
def _count_query(conn, cursor, statement, params, context, executemany):
    """SQLAlchemy event listener to count queries."""
    _query_log.add_query(statement)


@contextmanager
def count_queries():
    """Context manager to count SQL queries during operation.

    Yields a QueryLog that can be inspected after the context exits.

    Example:
        with count_queries() as log:
            await get_users_with_posts()

        assert log.count == 2  # One query for users, one for posts
        assert log.query_types["SELECT"] == 2
    """
    _query_log.reset()
    try:
        yield _query_log
    finally:
        logger.debug(f"[QUERIES] Executed {_query_log.count} queries: {dict(_query_log.query_types)}")


def assert_query_count(expected: int, tolerance: int = 0, message: str | None = None) -> QueryLog:
    """Assert that the query count is within tolerance of expected.

    Args:
        expected: Expected number of queries
        tolerance: Allowed deviation from expected (default 0)
        message: Optional custom assertion message

    Returns:
        QueryLog with actual query statistics

    Raises:
        AssertionError: If query count is outside tolerance

    Example:
        async def test_single_query():
            with count_queries():
                user = await get_user(1)
            assert_query_count(1)  # Exactly 1 query
    """
    actual = _query_log.count
    deviation = abs(actual - expected)

    assert deviation <= tolerance, (
        message or f"Expected {expected}±{tolerance} queries, got {actual}. Types: {dict(_query_log.query_types)}"
    )

    logger.info(f"[QUERIES] Count assertion passed: {actual} queries (expected {expected}±{tolerance})")
    return _query_log


def assert_query_reduces(
    before_operation: Callable[[], Any],
    after_operation: Callable[[], Any],
    expected_reduction: int = 1,
    message: str | None = None,
) -> tuple[QueryLog, QueryLog]:
    """Assert that after_operation executes fewer queries than before_operation.

    Useful for validating query optimization improvements.

    Args:
        before_operation: Operation representing the unoptimized version
        after_operation: Operation representing the optimized version
        expected_reduction: Expected reduction in query count (default 1)
        message: Optional custom assertion message

    Returns:
        Tuple of (before_log, after_log)

    Example:
        def old_way():
            users = get_users()
            return [user.posts for user in users]  # N+1 queries

        def new_way():
            return get_users_with_posts()  # Single query with join

        before, after = assert_query_reduces(old_way, new_way, 5)
    """
    _query_log.reset()
    before_operation()
    before_count = _query_log.count
    before_log = QueryLog(
        count=before_count, statements=list(_query_log.statements), query_types=_query_log.query_types.copy()
    )

    _query_log.reset()
    after_operation()
    after_count = _query_log.count
    after_log = QueryLog(
        count=after_count, statements=list(_query_log.statements), query_types=_query_log.query_types.copy()
    )

    reduction = before_count - after_count

    assert reduction >= expected_reduction, (
        message
        or f"Query reduction {reduction} below expected {expected_reduction}. Before: {before_count}, After: {after_count}"
    )

    logger.info(f"[QUERIES] Reduction assertion passed: {before_count} → {after_count} queries (-{reduction})")
    return before_log, after_log


def detect_n_plus_one(log: QueryLog) -> list[str]:
    """Detect N+1 query patterns in the query log.

    N+1 pattern: Same query structure repeated with different parameters.

    Args:
        log: QueryLog to analyze

    Returns:
        List of normalized query patterns that appear multiple times

    Example:
        with count_queries() as log:
            users = get_users()
            for user in users:
                posts = get_posts_by_user(user.id)  # N+1 pattern

        patterns = detect_n_plus_one(log)
        assert len(patterns) == 0  # Should have no N+1 patterns
    """
    patterns = Counter()

    for stmt in log.statements:
        # Normalize: remove specific IDs and values
        normalized = re.sub(r"= \d+", "= ?", stmt)
        normalized = re.sub(r"= '[^']*'", "= ?", normalized)
        normalized = re.sub(r"= \([^)]+\)", "= (?)", normalized)
        normalized = re.sub(r"IN \([^)]+\)", "IN (?)", normalized)
        patterns[normalized] += 1

    n_plus_one_patterns = [pattern for pattern, count in patterns.items() if count > 1]
    return n_plus_one_patterns


def assert_no_n_plus_one(log: QueryLog, message: str | None = None) -> None:
    """Assert that no N+1 query patterns exist in the query log.

    Args:
        log: QueryLog to analyze
        message: Optional custom assertion message

    Raises:
        AssertionError: If N+1 patterns are detected

    Example:
        with count_queries() as log:
            users = get_users_with_posts()

        assert_no_n_plus_one(log)  # Should have no N+1 patterns
    """
    patterns = detect_n_plus_one(log)

    if patterns:
        pattern_summary = "\n  ".join(patterns[:5])  # Show first 5 patterns
        if len(patterns) > 5:
            pattern_summary += f"\n  ... and {len(patterns) - 5} more"

        raise AssertionError(message or f"N+1 query patterns detected ({len(patterns)}):\n  {pattern_summary}")


def log_query_summary(operation_name: str, log: QueryLog) -> None:
    """Log a summary of query execution for an operation.

    Args:
        operation_name: Name of the operation being logged
        log: QueryLog with metrics

    Example:
        with count_queries() as log:
            await my_operation()
        log_query_summary("my_operation", log)
    """
    summary = log.get_summary()
    logger.info(f"[QUERIES] {operation_name}: total={summary['total']}, types={summary['by_type']}")


class QueryProfiler:
    """Helper class for profiling query usage across multiple operations.

    Useful for comparing query patterns between different approaches.

    Example:
        profiler = QueryProfiler()

        def approach_a():
            users = get_users()
            return [user.posts for user in users]

        def approach_b():
            return get_users_with_posts()

        log_a = profiler.profile("approach_a", approach_a)
        log_b = profiler.profile("approach_b", approach_b)

        profiler.compare("approach_a", "approach_b")
    """

    def __init__(self):
        self.logs: dict[str, QueryLog] = {}

    def profile(self, name: str, operation: Callable[[], Any]) -> QueryLog:
        """Profile an operation and store the query log.

        Args:
            name: Name to identify this operation
            operation: Callable to execute and measure

        Returns:
            QueryLog with metrics
        """
        _query_log.reset()
        operation()
        log = QueryLog(
            count=_query_log.count,
            statements=list(_query_log.statements),
            query_types=_query_log.query_types.copy(),
        )
        self.logs[name] = log
        log_query_summary(name, log)
        return log

    def compare(self, name_a: str, name_b: str) -> dict[str, Any]:
        """Compare query usage between two profiled operations.

        Args:
            name_a: Name of first operation
            name_b: Name of second operation

        Returns:
            Dict with comparison metrics

        Raises:
            KeyError: If either operation hasn't been profiled
        """
        if name_a not in self.logs:
            raise KeyError(f"Operation '{name_a}' not profiled")
        if name_b not in self.logs:
            raise KeyError(f"Operation '{name_b}' not profiled")

        log_a = self.logs[name_a]
        log_b = self.logs[name_b]

        count_diff = log_b.count - log_a.count
        count_pct = ((log_a.count - log_b.count) / log_a.count) * 100 if log_a.count > 0 else 0

        comparison = {
            "name_a": name_a,
            "name_b": name_b,
            "count_a": log_a.count,
            "count_b": log_b.count,
            "count_diff": count_diff,
            "count_reduction_pct": count_pct,
            "types_a": dict(log_a.query_types),
            "types_b": dict(log_b.query_types),
        }

        logger.info(
            f"[QUERIES COMPARE] {name_a} vs {name_b}: queries {log_a.count} → {log_b.count} ({count_pct:+.2f}%)"
        )

        return comparison

    def get_report(self) -> dict[str, dict[str, Any]]:
        """Get a report of all profiled operations.

        Returns:
            Dict mapping operation names to query metrics
        """
        return {
            name: {
                "total": log.count,
                "by_type": dict(log.query_types),
                "n_plus_one_patterns": len(detect_n_plus_one(log)),
            }
            for name, log in self.logs.items()
        }

    def check_n_plus_one(self, name: str) -> list[str]:
        """Check for N+1 patterns in a profiled operation.

        Args:
            name: Name of the operation to check

        Returns:
            List of N+1 patterns found (empty if none)

        Raises:
            KeyError: If operation hasn't been profiled
        """
        if name not in self.logs:
            raise KeyError(f"Operation '{name}' not profiled")

        return detect_n_plus_one(self.logs[name])
