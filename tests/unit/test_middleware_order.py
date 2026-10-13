"""R1-16 guard: middleware registration order is a security contract.

Starlette executes middleware in LIFO order (last added runs first on the
request path). The stack in ``core/guard/ordering.py`` is added in this order
(innermost -> outermost):

    RequestID, AttackDetection, Tenant, RateLimit, SecurityHeaders,
    RequestBodyLimit, IPFilter, QueryProfiler, GZipFilter, CORS

``add_middleware`` inserts at position 0, so ``app.user_middleware`` is in
REQUEST EXECUTION order (outermost first):

    CORS -> GZip -> QueryProfiler -> IPFilter -> BodyLimit -> SecurityHeaders
    -> RateLimit -> Tenant -> AttackDetection -> RequestID

(CORS is outermost so guard short-circuits — IPFilter 403, RateLimit 429,
BodyLimit 413 — still carry Access-Control-Allow-Origin; a cross-origin
client would otherwise see an opaque network error instead of the status.)

Reordering these silently changes trust boundaries (e.g. RateLimit must run
before AttackDetection's expensive body scan AND before the Tenant lookup so
429s skip it (P1-15); IPFilter sets the ``ip_whitelisted`` state that
RateLimit reads). This test pins the order so a reorder fails CI instead of
shipping.
"""

import pytest


class TestMiddlewareOrder:
    def test_middleware_execution_order(self):
        """user_middleware (request execution order) matches the contract."""
        from fastapi.middleware.cors import CORSMiddleware

        from src.app.core.monitoring.request_timer import RequestTimer
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
            CORSMiddleware,  # outermost — wraps every guard short-circuit (C4)
            # T-3: inside CORS (which must stay outermost) and outside every
            # guard, so the reported span is the whole server-side cost.
            # Mounted disabled-by-default, so this changes the stack, not the
            # timings.
            RequestTimer,
            GZipFilterMiddleware,
            QueryProfilerMiddleware,
            IPFilterMiddleware,
            RequestBodyLimitMiddleware,
            SecurityHeadersMiddleware,
            RateLimitMiddleware,
            TenantMiddleware,
            AttackDetectionMiddleware,
            RequestIDMiddleware,
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

    def test_rate_limit_runs_before_tenant_on_request(self):
        """P1-15: RateLimit executes before TenantMiddleware on the request path.

        A 429 must reject before the tenant lookup (L1+L2+DB) runs. Safe
        because the middleware resolves tenant_id from JWT claims /
        request.state and never reads the tenant ContextVar; dep-layer
        ``is_rate_limited`` still sees the context since TenantMiddleware
        runs before the handler regardless.
        """
        from src.app.main import app
        from src.app.middleware.rate_limit import RateLimitMiddleware
        from src.app.middleware.tenant import TenantMiddleware

        registered = [m.cls for m in app.user_middleware]
        assert registered.index(RateLimitMiddleware) < registered.index(TenantMiddleware), (
            "RateLimit must execute before TenantMiddleware on the request "
            "path so 429s skip the tenant lookup (P1-15)."
        )

    def test_cors_headers_present_on_guard_rejection(self):
        """C4 regression: a guard short-circuit (429 here) still carries CORS
        headers, so cross-origin clients can read the real status instead of
        an opaque network error."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from src.app.core.guard import resolve_guard_order
        from src.app.core.security.rate_policy import RateLimitVerdict
        from src.app.core.security.request_admission import AdmissionOutcome
        from src.app.middleware.rate_limit import RateLimitMiddleware

        verdict = RateLimitVerdict(True, 7, 60, 0, 30, "k", {"Retry-After": "30"})

        class _Limited:
            async def evaluate(self, request):
                return AdmissionOutcome("limited", "127.0.0.1", verdict)

        class _Settings:
            class app:  # noqa: N801
                MAX_BODY_SIZE = 1024

            class cors:  # noqa: N801
                CORS_ORIGINS = ["https://app.example.com"]
                CORS_METHODS = ["GET"]
                CORS_HEADERS = ["X-Tenant-ID"]

        app = FastAPI()
        for cls, kwargs in resolve_guard_order(_Settings):
            if cls is RateLimitMiddleware:
                app.add_middleware(cls, admission=_Limited())
            else:
                app.add_middleware(cls, **kwargs)

        with TestClient(app) as client:
            response = client.get("/", headers={"Origin": "https://app.example.com"})

        assert response.status_code == 429
        assert response.headers["access-control-allow-origin"] == "https://app.example.com"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
