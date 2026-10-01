"""SQL Query Profiler for Development.

DEVELOPMENT MODE ONLY - Do not enable in production.

This module owns the CROSS-REQUEST view of query activity. It used to also
attach its own SQLAlchemy ``before/after_cursor_execute`` listeners, which
made it a second full profiler: both this module and
``middleware/query_profiler.py`` were gated on the same
``QUERY_PROFILER_ENABLED`` flag and both registered a listener pair on the
same engine, so every query was timed twice, every slow query logged twice,
and every N+1 finding reported twice. That is finding ``muse 12``.

The single listener pair now lives in ``QueryProfilerMiddleware``
(``_attach_global_listeners``), which is also where the per-request
``ContextVar`` stats and the ``X-Query-Count``/``X-Query-Time`` headers live.
That listener feeds BOTH consumers: its own per-request ``QueryStats`` and
the global tracker here, via :func:`track_global_query`.

Why keep the global tracker at all, when per-request tracking already exists?
They answer different questions. Per-request tracking sees an N+1 inside one
request. The global tracker sees a query pattern that no single request
repeats but that recurs across every request THIS PROCESS serves — the
connection-pool leak and the per-request ARQ fan-out that per-request counts
are structurally blind to. That is why this module is not dead code, and why
deleting it would have been a real capability loss.

SCOPE LIMIT, stated plainly so the next reader does not over-trust it: the
only attachment point is `QueryProfilerMiddleware._get_engine`
(`middleware/query_profiler.py`), which runs on the HTTP path. The ARQ worker
builds its own engine through `core/db/database.py::_create_engine`, which no
longer wires profiling at all. So after the merge this tracker is web-request
only; it does NOT observe background-task queries.

Memory is bounded two ways: :func:`reset_query_tracker` clears it on
demand, and :func:`_evict_stale_tracker_entries` purges entries older than
an hour every ~1000 queries, so a process that never calls the reset does
not grow without limit.

Provides:
- Slow query detection and logging
- N+1 query pattern detection (per-request and global)
- Query execution time tracking
"""

import re
import time
from collections import defaultdict
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from src.app.core.logger import get_logger

# Create dedicated logger for query profiling
logger = get_logger(__name__)

# Track repeated queries for N+1 detection (global across all requests)
_query_tracker: dict[str, dict[str, Any]] = {}


# R4-18: threading.RLock blocks the event loop in async context.
# The global tracker is dev-only and races are acceptable; per-request
# tracking already uses ContextVar for async safety. Use a no-op lock.
# P3-3: deliberate trade-off — dev-only profiler, do not "fix" the lock
# back into an event-loop-blocking RLock.
from contextlib import nullcontext

_query_tracker_lock = nullcontext()
# Per-request query tracking using ContextVar for async safety
_request_queries: ContextVar[dict[str, int] | None] = ContextVar("request_queries", default=None)


# The engine listener pair that used to live here (setup_query_profiler) was
# the SECOND full profiler: it registered the same before/after cursor-execute
# events on the same engine as QueryProfilerMiddleware, behind the same
# QUERY_PROFILER_ENABLED flag, so every query was instrumented twice. The
# single pair now lives in middleware/query_profiler.py, which calls
# track_global_query() below. Do not re-add a second attachment here;
# tests/unit/core/db/test_profiler.py asserts the engine carries exactly one
# listener per cursor-execute event.


def track_global_query(statement: str, n_plus_1_threshold: int = 3) -> None:
    """Record one statement in the CROSS-REQUEST tracker.

    Called by the single SQLAlchemy listener in
    ``middleware/query_profiler.py`` so that the engine is instrumented
    exactly once while still feeding both the per-request and the global
    view.

    Args:
        statement: Raw SQL statement.
        n_plus_1_threshold: Warn when the same pattern reaches this count.
    """
    _track_query(_normalize_query(statement), n_plus_1_threshold, detailed_logs=False)


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
                    "query_tracker_n_plus_1_detected",
                    count=tracker["count"],
                    pattern=normalized_statement[:200] + ("..." if len(normalized_statement) > 200 else ""),
                    first_seen=tracker["first_seen_at"],
                )
            elif tracker["count"] > n_plus_1_threshold and detailed_logs:
                logger.info(
                    "query_tracker_n_plus_1_continued",
                    count=tracker["count"],
                    pattern=normalized_statement[:200] + ("..." if len(normalized_statement) > 200 else ""),
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
        logger.debug("query_tracker_evicted_stale_entries", evicted=len(stale_keys))


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
                "request_n_plus_1_detected",
                count=count,
                path=request_path,
                pattern=query_pattern[:200] + ("..." if len(query_pattern) > 200 else ""),
            )
        elif detailed_logs and count > 1:
            logger.info(
                "request_query_repeated",
                count=count,
                path=request_path,
                pattern=query_pattern[:200] + ("..." if len(query_pattern) > 200 else ""),
            )

    # Clear the request tracking
    _request_queries.set(None)
    logger.debug("request_query_tracking_ended", found=len(n_plus_1_patterns))

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
            "request_n_plus_1_threshold_reached",
            count=n_plus_1_threshold,
            pattern=normalized_statement[:200] + ("..." if len(normalized_statement) > 200 else ""),
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
    "track_global_query",
    "reset_query_tracker",
    "get_query_stats",
    "is_profiler_enabled",
    "start_request_tracking",
    "end_request_tracking",
    "get_request_query_stats",
]
