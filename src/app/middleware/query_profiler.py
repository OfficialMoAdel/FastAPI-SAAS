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

import threading
import time
from collections import defaultdict
from contextvars import ContextVar
from typing import Any, cast

from fastapi import Request, Response
from sqlalchemy import event
from sqlalchemy.engine import Connection, Engine
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from src.app.core.config import EnvironmentOption, settings
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


_query_stats_ctx: ContextVar[QueryStats | None] = ContextVar("query_stats_ctx", default=None)
_profiler_initialized = False
_profiler_lock = threading.Lock()


def _attach_global_listeners(engine: Engine) -> None:
    """Attach global listeners exactly once."""
    global _profiler_initialized
    if _profiler_initialized:
        return
    with _profiler_lock:
        if _profiler_initialized:
            return

        @event.listens_for(engine, "before_cursor_execute")
        def before_execute(conn: Connection, cursor, statement, parameters, context, executemany):
            stats = _query_stats_ctx.get()
            if stats is not None:
                conn.info["middleware_query_start_time"] = time.perf_counter()

        @event.listens_for(engine, "after_cursor_execute")
        def after_execute(conn: Connection, cursor, statement, parameters, context, executemany):
            stats = _query_stats_ctx.get()
            if stats is not None:
                start_time = conn.info.get("middleware_query_start_time")
                if start_time:
                    duration_ms = (time.perf_counter() - start_time) * 1000
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

        _profiler_initialized = True


class QueryProfilerMiddleware(BaseHTTPMiddleware):
    """Middleware to profile SQL queries during request processing."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self._is_enabled = self._check_enabled()
        self._engine: Engine | None = None

    def _check_enabled(self) -> bool:
        if settings.ENVIRONMENT == EnvironmentOption.PRODUCTION:
            return False
        enabled = getattr(settings, "QUERY_PROFILER_ENABLED", None)
        if enabled is not None:
            return bool(enabled)
        return True

    def _get_engine(self) -> Engine | None:
        if self._engine is None:
            try:
                self._engine = async_engine.sync_engine
                if self._engine:
                    _attach_global_listeners(self._engine)
            except AttributeError:
                logger.debug("Could not access sync engine for query profiling")
                return None
        return self._engine

    async def dispatch(self, request: Request, call_next) -> Response:
        if not self._is_enabled:
            return cast(Response, await call_next(request))

        engine = self._get_engine()
        if not engine:
            response = await call_next(request)
            response = cast(Response, response)
            response.headers["X-Query-Count"] = "0"
            response.headers["X-Query-Time"] = "0"
            return response

        stats = QueryStats()
        token = _query_stats_ctx.set(stats)
        start_time = time.perf_counter()

        try:
            response = await call_next(request)
        finally:
            total_time_ms = (time.perf_counter() - start_time) * 1000
            self._log_results(request, stats, total_time_ms)
            _query_stats_ctx.reset(token)

        response = cast(Response, response)
        response.headers["X-Query-Count"] = str(len(stats.queries))
        response.headers["X-Query-Time"] = str(round(stats.total_time_ms, 3))
        if stats.slow_queries:
            response.headers["X-Slow-Queries"] = str(len(stats.slow_queries))

        return response

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
                logger.warning("Potential N+1", count=count, query_snippet=query[:80])
