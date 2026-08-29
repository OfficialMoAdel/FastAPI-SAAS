"""R1-16 guard: middleware registration order is a security contract.

Starlette executes middleware in LIFO order (last added runs first on the
request path). The stack in ``main.py`` is added in this order
(innermost -> outermost):

    RequestID, AttackDetection, RateLimit, Tenant, SecurityHeaders,
    RequestBodyLimit, IPFilter, QueryProfiler, GZipFilter

``add_middleware`` inserts at position 0, so ``app.user_middleware`` is in
REQUEST EXECUTION order (outermost first):

    GZip -> QueryProfiler -> IPFilter -> BodyLimit -> SecurityHeaders
    -> Tenant -> RateLimit -> AttackDetection -> RequestID -> CORS

(CORSMiddleware is innermost: it is added first, inside create_application.)

Reordering these silently changes trust boundaries (e.g. RateLimit must run
before AttackDetection's expensive body scan; IPFilter sets the
``ip_whitelisted`` state that RateLimit reads). This test pins the order so a
reorder fails CI instead of shipping.
"""

import pytest


class TestMiddlewareOrder:
    def test_middleware_execution_order(self):
        """user_middleware (request execution order) matches the contract."""
        from fastapi.middleware.cors import CORSMiddleware

        from src.app.main import app
        from src.app.middleware.attack_detection import AttackDetectionMiddleware
        from src.app.middleware.body_limit import RequestBodyLimitMiddleware
        from src.app.middleware.gzip_filter import GZipFilterMiddleware
        from src.app.middleware.ip_filter import IPFilterMiddleware
        from src.app.middleware.query_profiler import QueryProfilerMiddleware
        from src.app.middleware.rate_limit import RateLimitMiddleware
        from src.app.middleware.request_id import RequestIDMiddleware
        from src.app.middleware.security_headers import SecurityHeadersMiddleware
        from src.app.middleware.tenant import TenantMiddleware

        # app.user_middleware is in request execution order (outermost first):
        # add_middleware inserts at index 0.
        registered = [m.cls for m in app.user_middleware]

        expected = [
            GZipFilterMiddleware,  # outermost — runs first on request
            QueryProfilerMiddleware,
            IPFilterMiddleware,
            RequestBodyLimitMiddleware,
            SecurityHeadersMiddleware,
            TenantMiddleware,
            RateLimitMiddleware,
            AttackDetectionMiddleware,
            RequestIDMiddleware,
            CORSMiddleware,  # innermost — added first in create_application
        ]

        assert registered == expected, (
            "Middleware order changed. This reorders the request execution "
            "stack and can break rate-limit/tenant trust boundaries. If "
            "intentional, update main.py's ordering comment AND this test "
            "together.\n"
            f"Got:      {[c.__name__ for c in registered]}\n"
            f"Expected: {[c.__name__ for c in expected]}"
        )

    def test_rate_limit_runs_before_attack_detection_on_request(self):
        """RateLimit executes before AttackDetection's expensive body scan.

        The R4 FIX moved AttackDetection ahead of RateLimit in add_middleware
        order so the cheap 429 path runs first. In execution order
        (user_middleware) RateLimit must therefore appear BEFORE
        AttackDetection.
        """
        from src.app.main import app
        from src.app.middleware.attack_detection import AttackDetectionMiddleware
        from src.app.middleware.rate_limit import RateLimitMiddleware

        registered = [m.cls for m in app.user_middleware]
        assert registered.index(RateLimitMiddleware) < registered.index(AttackDetectionMiddleware), (
            "RateLimit must execute before AttackDetection on the request "
            "path (cheap 429 before expensive body-buffering scan)."
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
