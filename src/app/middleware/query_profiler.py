"""Query Profiler Middleware for development mode.

Tracks and logs slow SQL queries during development to help identify N+1 issues,
missing indexes, and inefficient queries before they reach production.

Features:
- Logs queries exceeding configurable threshold (default 100ms)
- Tracks query count per request
- Identifies repeated identical queries (N+1 detection)
- Provides detailed timing breakdowns
- Only active in non-production environments
"""

import time
from collections import defaultdict
from typing import Any, cast

from fastapi import Request, Response
from sqlalchemy import event
from sqlalchemy.engine import Connection, Engine
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from src.app.core.config import settings
from src.app.core.db.database import async_engine
from src.app.core.logger import get_logger

logger = get_logger(__name__)

# Default configuration
DEFAULT_SLOW_QUERY_THRESHOLD_MS = 100  # Log queries slower than 100ms
DEFAULT_N_PLUS_1_THRESHOLD = 3  # Warn if same query executed >3 times
MAX_QUERIES_TO_LOG = 50  # Limit log spam for very chatty endpoints


class QueryStats:
    """Container for query statistics during a request."""

    def __init__(self) -> None:
        self.queries: list[dict[str, Any]] = []
        self.total_time_ms: float = 0.0
        self.query_counts: dict[str, int] = defaultdict(int)
        self.slow_queries: list[dict[str, Any]] = []
        self._before_listener: Any = None
        self._after_listener: Any = None

    def add_query(self, statement: str, params: dict, duration_ms: float) -> None:
        """Record a query execution."""
        # Normalize statement for deduplication (remove whitespace variations)
        normalized = " ".join(statement.split())

        query_info = {
            "statement": statement[:500],  # Truncate very long queries
            "normalized": normalized[:200],
            "params": str(params)[:200] if params else None,
            "duration_ms": round(duration_ms, 2),
        }

        self.queries.append(query_info)
        self.total_time_ms += duration_ms
        self.query_counts[normalized[:200]] += 1

        # Track slow queries separately
        threshold = getattr(settings, "QUERY_PROFILER_SLOW_THRESHOLD_MS", DEFAULT_SLOW_QUERY_THRESHOLD_MS)
        if duration_ms > threshold:
            self.slow_queries.append(query_info)


class QueryProfilerMiddleware(BaseHTTPMiddleware):
    """Middleware to profile SQL queries during request processing.

    Only active when:
    - ENVIRONMENT is not "production"
    - QUERY_PROFILER_ENABLED setting is True (or unset, defaults to enabled in dev)

    Logs summary at end of request including:
    - Total query count
    - Total query time
    - Slow queries (>100ms by default)
    - Potential N+1 issues (repeated queries)
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self._is_enabled = self._check_enabled()
        self._engine: Engine | None = None

    def _check_enabled(self) -> bool:
        """Check if profiler should be enabled."""
        # Never enable in production
        if settings.ENVIRONMENT.value == "production":
            return False

        # Check explicit setting
        enabled = getattr(settings, "QUERY_PROFILER_ENABLED", None)
        if enabled is not None:
            return bool(enabled)

        # Default: enabled in local/staging
        return True

    def _get_engine(self) -> Engine | None:
        """Get the sync engine from async engine."""
        if self._engine is None:
            try:
                # Access the sync engine through the async engine's pool
                self._engine = async_engine.sync_engine
            except AttributeError:
                logger.debug("Could not access sync engine for query profiling")
                return None
        return self._engine

    async def dispatch(self, request: Request, call_next) -> Response:
        """Process request with query profiling."""
        if not self._is_enabled:
            response = await call_next(request)
            response = cast(Response, response)
            response.headers["X-Query-Count"] = "0"
            response.headers["X-Query-Time"] = "0"
            return response

        stats = QueryStats()
        engine = self._get_engine()

        if not engine:
            # Can't profile without engine access - still add zero-value headers
            response = await call_next(request)
            response = cast(Response, response)
            response.headers["X-Query-Count"] = "0"
            response.headers["X-Query-Time"] = "0"
            return response

        # Set up query listener
        self._attach_listener(engine, stats)

        start_time = time.perf_counter()

        try:
            response = await call_next(request)
        finally:
            # Calculate total request time
            total_time_ms = (time.perf_counter() - start_time) * 1000

            # Detach listener
            self._detach_listener(engine, stats)

            # Log profiling results
            self._log_results(request, stats, total_time_ms)

        # Add profiling headers to response
        response = cast(Response, response)
        response.headers["X-Query-Count"] = str(len(stats.queries))
        response.headers["X-Query-Time"] = str(round(stats.total_time_ms, 3))
        if stats.slow_queries:
            response.headers["X-Slow-Queries"] = str(len(stats.slow_queries))

        return response

    def _attach_listener(self, engine: Engine, stats: QueryStats) -> None:
        """Attach SQL execution event listener."""

        @event.listens_for(engine, "before_cursor_execute")
        def before_execute(conn: Connection, cursor, statement, parameters, context, executemany):
            conn.info["query_start_time"] = time.perf_counter()

        @event.listens_for(engine, "after_cursor_execute")
        def after_execute(conn: Connection, cursor, statement, parameters, context, executemany):
            start_time = conn.info.get("query_start_time")
            if start_time:
                duration_ms = (time.perf_counter() - start_time) * 1000
                # SQLAlchemy parameters may be a dict (named params) OR a tuple/list
                # (positional params). Safely convert to dict only when possible.
                if isinstance(parameters, dict):
                    safe_params = parameters
                elif parameters:
                    safe_params = {"positional": list(parameters)}
                else:
                    safe_params = {}
                stats.add_query(
                    statement=str(statement),
                    params=safe_params,
                    duration_ms=duration_ms,
                )

        # Store references for cleanup
        stats._before_listener = before_execute
        stats._after_listener = after_execute

    def _detach_listener(self, engine: Engine, stats: QueryStats) -> None:
        """Remove SQL execution event listeners."""
        if stats._before_listener is not None:
            event.remove(engine, "before_cursor_execute", stats._before_listener)
        if stats._after_listener is not None:
            event.remove(engine, "after_cursor_execute", stats._after_listener)

    def _log_results(self, request: Request, stats: QueryStats, total_time_ms: float) -> None:
        """Log query profiling results."""
        path = request.url.path
        method = request.method
        query_count = len(stats.queries)

        # Skip logging for health checks and static files
        if path in {"/health", "/health/live", "/health/ready", "/metrics"}:
            return

        if query_count == 0:
            return

        # Build log message
        log_parts = [
            f"method={method}",
            f"path={path}",
            f"queries={query_count}",
            f"query_time_ms={round(stats.total_time_ms, 2)}",
            f"total_time_ms={round(total_time_ms, 2)}",
        ]

        # Add database overhead percentage
        if total_time_ms > 0:
            db_percent = round((stats.total_time_ms / total_time_ms) * 100, 1)
            log_parts.append(f"db_time_pct={db_percent}%")

        # Detect N+1 issues
        n_plus_1_threshold = getattr(settings, "QUERY_PROFILER_N_PLUS_1_THRESHOLD", DEFAULT_N_PLUS_1_THRESHOLD)
        n_plus_1_queries = [(query, count) for query, count in stats.query_counts.items() if count > n_plus_1_threshold]

        if n_plus_1_queries:
            log_parts.append(f"n_plus_1_detected={len(n_plus_1_queries)}")

        # Log slow queries
        if stats.slow_queries:
            slow_count = len(stats.slow_queries)
            log_parts.append(f"slow_queries={slow_count}")

            # Log details of first few slow queries
            for i, sq in enumerate(stats.slow_queries[:3], 1):
                log_parts.append(f"slow_{i}={sq['duration_ms']}ms: {sq['statement'][:80]}...")

        # Determine log level based on severity
        if stats.slow_queries or n_plus_1_queries:
            logger.warning(" | ".join(log_parts))
        elif query_count > 20:  # Warn about chatty endpoints
            logger.warning(" | ".join(log_parts))
        else:
            logger.info(" | ".join(log_parts))

        # Detailed debug log for N+1 issues
        if n_plus_1_queries and getattr(settings, "QUERY_PROFILER_DETAILED_LOGS", False):
            for query, count in sorted(n_plus_1_queries, key=lambda x: -x[1])[:3]:
                logger.warning(f"Potential N+1: executed {count} times | {query[:100]}...")
