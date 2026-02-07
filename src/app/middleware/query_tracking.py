"""Query Tracking Middleware for Development.

DEVELOPMENT MODE ONLY - Tracks queries per request for N+1 detection.

Usage:
    Add to FastAPI app in main.py:

    from src.app.middleware.query_tracking import QueryTrackingMiddleware
    from src.app.core.db.profiler import is_profiler_enabled
    from src.app.core.config import settings

    if settings.QUERY_PROFILER_ENABLED and is_profiler_enabled(settings.ENVIRONMENT.value):
        app.add_middleware(QueryTrackingMiddleware)
"""

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from src.app.core.db.profiler import end_request_tracking, start_request_tracking
from src.app.core.logger import get_logger

logger = get_logger(__name__)


class QueryTrackingMiddleware(BaseHTTPMiddleware):
    """Middleware to track queries per request for N+1 detection.

    This middleware should be added in development mode only.
    It wraps each request with query tracking to detect N+1 patterns.

    Example:
        app.add_middleware(QueryTrackingMiddleware)
    """

    async def dispatch(self, request: Request, call_next):
        """Process request with query tracking enabled.

        Args:
            request: FastAPI request object
            call_next: Next middleware/handler in chain

        Returns:
            Response from next handler
        """
        # Start tracking queries for this request
        start_request_tracking()

        try:
            # Process the request
            response = await call_next(request)
            return response
        finally:
            # Always end tracking, even if request fails
            try:
                end_request_tracking(
                    request_path=request.url.path,
                    threshold=3,
                    detailed_logs=False,
                )
            except Exception as e:
                # Don't let profiler errors affect the request
                logger.debug("Error ending query tracking: %s", e)
