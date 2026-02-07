"""FastAPI middleware components for request processing.

Middleware order in application:
1. GZipMiddleware (outermost - compresses responses)
2. IPFilterMiddleware (IP whitelist/blacklist enforcement)
3. SecurityHeadersMiddleware (CSP, HSTS, etc.)
4. BodyLimitMiddleware (request size limits)
5. TenantMiddleware (multi-tenant context)
6. RateLimitMiddleware (tier-aware rate limiting)
7. QueryProfilerMiddleware (dev-mode query analysis)
8. RequestIDMiddleware (innermost - request tracing)
"""

from .body_limit import RequestBodyLimitMiddleware
from .ip_filter import IPFilterMiddleware
from .query_profiler import QueryProfilerMiddleware
from .rate_limit import RateLimitMiddleware
from .request_id import RequestIDMiddleware
from .security_headers import SecurityHeadersMiddleware
from .tenant import TenantMiddleware

__all__ = [
    "RequestBodyLimitMiddleware",
    "IPFilterMiddleware",
    "QueryProfilerMiddleware",
    "RateLimitMiddleware",
    "RequestIDMiddleware",
    "SecurityHeadersMiddleware",
    "TenantMiddleware",
]
