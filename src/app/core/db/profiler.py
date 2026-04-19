"""SQL Query Profiler for Development.

DEVELOPMENT MODE ONLY - Do not enable in production.

Provides:
- Slow query detection and logging
- N+1 query pattern detection (per-request and global)
- Query execution time tracking
"""

import re
import threading
import time
from collections import defaultdict
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine

from src.app.core.logger import get_logger

# Create dedicated logger for query profiling
logger = get_logger(__name__)

# Track repeated queries for N+1 detection (global across all requests)
_query_tracker: dict[str, dict[str, Any]] = {}
_query_tracker_lock = threading.RLock()

# Per-request query tracking using ContextVar for async safety
_request_queries: ContextVar[dict[str, int] | None] = ContextVar("request_queries", default=None)


def setup_query_profiler(
    engine: AsyncEngine,
    slow_query_threshold_ms: int = 100,
    n_plus_1_threshold: int = 3,
    detailed_logs: bool = False,
) -> None:
    """Attach query profiler to SQLAlchemy engine.

    DEVELOPMENT MODE ONLY - Do not enable in production.

    Logs queries slower than threshold with:
    - Duration in milliseconds
    - Truncated SQL (500 chars max)
    - Stack trace hint (optional)
    - N+1 query pattern detection

    Args:
        engine: SQLAlchemy async engine
        slow_query_threshold_ms: Threshold for 'slow' query (default: 100ms)
        n_plus_1_threshold: Warn if same query executed more than N times (default: 3)
        detailed_logs: Log detailed query analysis including repeated query samples
    """
    # Access the sync engine for event listening
    sync_engine = engine.sync_engine

    @event.listens_for(sync_engine, "before_cursor_execute")
    def before_execute(
        conn: Any,
        cursor: Any,
        statement: str,
        parameters: tuple[Any, ...],
        context: Any,
        executemany: bool,
    ) -> None:
        """Record query start time."""
        conn.info["query_start_time"] = time.perf_counter()
        conn.info["query_statement"] = statement

    @event.listens_for(sync_engine, "after_cursor_execute")
    def after_execute(
        conn: Any,
        cursor: Any,
        statement: str,
        parameters: tuple[Any, ...],
        context: Any,
        executemany: bool,
    ) -> None:
        """Log slow queries and detect N+1 patterns."""
        start_time = conn.info.get("query_start_time")
        if start_time is None:
            return

        # Calculate elapsed time in milliseconds
        elapsed_ms = (time.perf_counter() - start_time) * 1000

        # Create normalized query signature for N+1 detection
        # Remove specific values but keep structure
        normalized_statement = _normalize_query(statement)

        # Track query for N+1 detection (global tracker)
        _track_query(normalized_statement, n_plus_1_threshold, detailed_logs)

        # Track query for per-request N+1 detection
        _track_request_query(normalized_statement, n_plus_1_threshold)

        # Log slow queries
        if elapsed_ms > slow_query_threshold_ms:
            # Truncate parameters to prevent huge log entries
            params_str = str(parameters)
            if len(params_str) > 500:
                params_str = params_str[:500] + "..."

            logger.warning(
                "SLOW QUERY: %.2fms (threshold: %dms)\n  SQL: %s\n  Params: %s",
                elapsed_ms,
                slow_query_threshold_ms,
                statement[:500] + ("..." if len(statement) > 500 else ""),
                params_str,
            )


def _normalize_query(statement: str) -> str:
    """Normalize SQL statement for N+1 detection.

    Strips out specific values while preserving query structure.
    This allows detection of repeated query patterns.

    Args:
        statement: Raw SQL statement

    Returns:
        Normalized statement suitable for pattern matching
    """
    # Simple normalization: replace string literals and numbers with placeholders

    normalized = statement

    # Replace string literals with '?'
    normalized = re.sub(r"'[^']*'", "'?'", normalized)
    # Replace numbers with '?'
    normalized = re.sub(r"\b\d+\b", "?", normalized)

    return normalized.strip()


def _track_query(
    normalized_statement: str,
    n_plus_1_threshold: int,
    detailed_logs: bool,
) -> None:
    """Track query execution for N+1 pattern detection.

    Args:
        normalized_statement: Normalized SQL statement
        n_plus_1_threshold: Warn if same query executed more than N times
        detailed_logs: Log detailed analysis
    """
    now = time.perf_counter()

    with _query_tracker_lock:
        # BUG FIX: Opportunistically evict stale entries every ~1000 queries.
        # Prevents unbounded memory growth in long-running processes where
        # reset_query_tracker() was never called from the request lifecycle.
        if len(_query_tracker) % 1000 == 0 and _query_tracker:
            _evict_stale_tracker_entries()

        if normalized_statement in _query_tracker:
            tracker = _query_tracker[normalized_statement]
            tracker["count"] += 1
            tracker["last_seen"] = now

            # Check if we've hit the N+1 threshold
            if tracker["count"] == n_plus_1_threshold:
                logger.warning(
                    "N+1 QUERY DETECTED: Same query pattern executed %d times.\n  Pattern: %s\n  First seen: %s",
                    tracker["count"],
                    normalized_statement[:200] + ("..." if len(normalized_statement) > 200 else ""),
                    tracker["first_seen_at"],
                )
            elif tracker["count"] > n_plus_1_threshold and detailed_logs:
                logger.info(
                    "N+1 CONTINUED: Same query pattern executed %d times total.\n  Pattern: %s",
                    tracker["count"],
                    normalized_statement[:200] + ("..." if len(normalized_statement) > 200 else ""),
                )
        else:
            # First time seeing this query pattern
            _query_tracker[normalized_statement] = {
                "count": 1,
                "first_seen_at": datetime.now(UTC).isoformat(),
                "last_seen": now,
            }


def reset_query_tracker() -> None:
    """Reset the N+1 query tracker.

    Call this periodically (e.g., between requests) to clear old tracking state.
    """
    with _query_tracker_lock:
        _query_tracker.clear()
    logger.debug("Query tracker reset")


# BUG FIX: Maximum age (seconds) for entries in _query_tracker before automatic eviction.
# Previously the dict grew unbounded because reset_query_tracker() was never called.
# Entries older than this are purged during the next _track_query call.
_QUERY_TRACKER_MAX_AGE_SECONDS = 3600  # 1 hour


def _evict_stale_tracker_entries() -> None:
    """Evict _query_tracker entries that haven't been seen for over an hour.

    Called opportunistically from _track_query to bound memory growth without
    requiring an explicit periodic reset from the request lifecycle.
    """
    # Note: assumed to be called within _query_tracker_lock
    cutoff = time.perf_counter() - _QUERY_TRACKER_MAX_AGE_SECONDS
    stale_keys = [k for k, v in _query_tracker.items() if v.get("last_seen", 0) < cutoff]
    for k in stale_keys:
        del _query_tracker[k]
    if stale_keys:
        logger.debug("Query tracker evicted %d stale entries", len(stale_keys))


def get_query_stats() -> dict[str, dict[str, Any]]:
    """Get current query statistics for debugging.

    Returns:
        Dictionary of normalized queries to their execution counts
    """
    with _query_tracker_lock:
        return {
            statement: {"count": data["count"], "first_seen": data["first_seen_at"]}
            for statement, data in _query_tracker.items()
        }



def is_profiler_enabled(environment: str) -> bool:
    """Check if profiler should be enabled based on environment.

    Profiler is auto-disabled in production for safety.

    Args:
        environment: Current environment setting

    Returns:
        True if profiler can be enabled
    """
    return environment.lower() in ("local", "development", "dev", "staging")


def start_request_tracking() -> None:
    """Start tracking queries for the current request.

    Call this at the beginning of each request (e.g., in middleware)
    to enable per-request N+1 detection.
    """
    _request_queries.set(defaultdict(int))
    logger.debug("Request query tracking started")


def end_request_tracking(
    request_path: str,
    threshold: int = 3,
    detailed_logs: bool = False,
) -> dict[str, int]:
    """End tracking and report N+1 patterns for the current request.

    Call this at the end of each request (e.g., in middleware finally block)
    to detect and log N+1 query patterns.

    Args:
        request_path: The URL path of the request (for logging)
        threshold: Minimum count to trigger N+1 warning (default: 3)
        detailed_logs: If True, log all repeated queries, not just threshold violations

    Returns:
        Dictionary of detected N+1 patterns and their counts
    """
    queries = _request_queries.get()
    if queries is None:
        return {}

    n_plus_1_patterns: dict[str, int] = {}

    for query_pattern, count in queries.items():
        if count >= threshold:
            n_plus_1_patterns[query_pattern] = count
            logger.warning(
                "N+1 QUERY DETECTED (per-request): %d executions of same pattern\n  Path: %s\n  Pattern: %s",
                count,
                request_path,
                query_pattern[:200] + ("..." if len(query_pattern) > 200 else ""),
            )
        elif detailed_logs and count > 1:
            logger.info(
                "REPEATED QUERY (per-request): %d executions\n  Path: %s\n  Pattern: %s",
                count,
                request_path,
                query_pattern[:200] + ("..." if len(query_pattern) > 200 else ""),
            )

    # Clear the request tracking
    _request_queries.set(None)
    logger.debug("Request query tracking ended, found %d N+1 patterns", len(n_plus_1_patterns))

    return n_plus_1_patterns


def _track_request_query(
    normalized_statement: str,
    n_plus_1_threshold: int,
) -> None:
    """Track a query for per-request N+1 detection.

    This is called by the SQLAlchemy event listener for each executed query.

    Args:
        normalized_statement: Normalized SQL statement
        n_plus_1_threshold: Threshold for warning (used for immediate logging)
    """
    queries = _request_queries.get()
    if queries is None:
        return  # Not in a tracked request context

    queries[normalized_statement] += 1

    # Optional: Log immediately when threshold is hit for real-time detection
    if queries[normalized_statement] == n_plus_1_threshold:
        logger.warning(
            "N+1 THRESHOLD REACHED: Query pattern executed %d times in current request\n  Pattern: %s",
            n_plus_1_threshold,
            normalized_statement[:200] + ("..." if len(normalized_statement) > 200 else ""),
        )


def get_request_query_stats() -> dict[str, int] | None:
    """Get current request's query statistics.

    Useful for debugging during request processing.

    Returns:
        Dictionary of query patterns to execution counts, or None if not tracking
    """
    queries = _request_queries.get()
    if queries is None:
        return None
    return dict(queries)


# Export all public functions
__all__ = [
    "setup_query_profiler",
    "reset_query_tracker",
    "get_query_stats",
    "is_profiler_enabled",
    "start_request_tracking",
    "end_request_tracking",
    "get_request_query_stats",
]

