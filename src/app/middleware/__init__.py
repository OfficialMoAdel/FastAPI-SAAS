"""FastAPI middleware components for request processing.

Middleware order in application (Outermost to Innermost):
1. GZipMiddleware           (compresses all responses)
2. QueryProfilerMiddleware  (dev-mode query analysis)
3. IPFilterMiddleware       (IP whitelist/blacklist enforcement)
4. RequestBodyLimitMiddleware (request size limits)
5. AttackDetectionMiddleware  (detects SQLi, XSS, path traversal)
6. SecurityHeadersMiddleware  (CSP, HSTS, etc.)
7. TenantMiddleware         (multi-tenant context)
8. RateLimitMiddleware      (tier-aware rate limiting)
9. RequestIDMiddleware      (request tracing)
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
