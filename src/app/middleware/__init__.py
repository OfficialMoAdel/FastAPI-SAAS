"""FastAPI middleware components for request processing.

Ordering lives in ONE place: ``src/app/core/guard/ordering.py``
(``GUARD_ORDER``). This package only re-exports the middleware classes.
"""

from src.app.middleware.attack_detection import AttackDetectionMiddleware
from src.app.middleware.body_limit import RequestBodyLimitMiddleware
from src.app.middleware.ip_filter import IPFilterMiddleware
from src.app.middleware.query_profiler import QueryProfilerMiddleware
from src.app.middleware.rate_limit import RateLimitMiddleware
from src.app.middleware.request_id import RequestIDMiddleware
from src.app.middleware.security_headers import SecurityHeadersMiddleware
from src.app.middleware.tenant import TenantMiddleware

__all__ = [
    "AttackDetectionMiddleware",
    "RequestBodyLimitMiddleware",
    "IPFilterMiddleware",
    "QueryProfilerMiddleware",
    "RateLimitMiddleware",
    "RequestIDMiddleware",
    "SecurityHeadersMiddleware",
    "TenantMiddleware",
]
