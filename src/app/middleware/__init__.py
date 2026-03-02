"""FastAPI middleware components for request processing.

Middleware order in application:
1. GZipMiddleware (outermost - compresses responses)
2. IPFilterMiddleware (IP whitelist/blacklist enforcement)
3. AttackDetectionMiddleware (detects SQLi, XSS, path traversal)
4. SecurityHeadersMiddleware (CSP, HSTS, etc.)
5. BodyLimitMiddleware (request size limits)
6. TenantMiddleware (multi-tenant context)
7. RateLimitMiddleware (tier-aware rate limiting)
8. QueryProfilerMiddleware (dev-mode query analysis)
9. RequestIDMiddleware (innermost - request tracing)
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
