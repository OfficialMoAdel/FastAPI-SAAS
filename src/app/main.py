"""FastAPI SaaS application entry point.

Thin composition root (P3-#15): the app factory, the lifespan, and the
middleware stack. All startup/shutdown orchestration lives in
``src.app.core.setup`` (``lifespan_with_admin`` + phases); nothing is
wired here.
"""

from dotenv import load_dotenv

load_dotenv()

from fastapi.responses import ORJSONResponse

from src.app.api import get_router
from src.app.core.config import settings
from src.app.core.setup import create_application, lifespan_with_admin
from src.app.middleware.attack_detection import AttackDetectionMiddleware
from src.app.middleware.body_limit import RequestBodyLimitMiddleware
from src.app.middleware.gzip_filter import GZipFilterMiddleware
from src.app.middleware.ip_filter import IPFilterMiddleware
from src.app.middleware.query_profiler import QueryProfilerMiddleware
from src.app.middleware.rate_limit import RateLimitMiddleware
from src.app.middleware.request_id import RequestIDMiddleware
from src.app.middleware.security_headers import SecurityHeadersMiddleware
from src.app.middleware.tenant import TenantMiddleware

app = create_application(
    router=get_router(),
    settings=settings,
    lifespan=lifespan_with_admin,
    default_response_class=ORJSONResponse,
)


# Middleware stack (LIFO - last added runs first on request)
# Request flow:
#   GZip → QueryProfiler → IPFilter → BodyLimit → SecurityHeaders
#     → RateLimit → Tenant → AttackDetection → RequestID → CORS → Handler
# Response flow:
#   Handler → CORS → RequestID → AttackDetection → Tenant → RateLimit
#     → SecurityHeaders → BodyLimit → IPFilter → QueryProfiler → GZip
#
# Pinned by tests/unit/test_middleware_order.py (R1-16 security contract).
#
# Key ordering decisions:
# - RequestID: Added first = innermost (closest to handler for accurate tracing)
# - RateLimit: Runs before Tenant so a cheap 429 never pays the tenant
#   lookup (L1+L2+DB). Safe: the middleware resolves tenant_id from JWT
#   claims/request.state, never from the tenant ContextVar (P1-15; the old
#   P2-5 Tenant-first rationale is stale — nothing in the rate-limit request
#   path reads get_current_tenant).
# - Tenant: Extract tenant context for multi-tenant features
# - SecurityHeaders: Add security headers to all responses
# - AttackDetection: Registered before RateLimit so it runs AFTER it in LIFO —
#   the expensive body-buffering scan never runs for requests a cheap 429
#   rejects (R4). On the request path it sits after BodyLimit, so oversized
#   payloads are rejected by 413 before the scan buffers them.
# - BodyLimit: Reject oversized requests early
# - IPFilter: Block unwanted IPs early, sets ip_whitelisted state for RateLimit
# - GZip: Added last = outermost (compresses ALL responses including 4xx/5xx errors)
app.add_middleware(RequestIDMiddleware)  # Innermost - must be first for accurate tracing
# R4 FIX: AttackDetection is now registered BEFORE RateLimitMiddleware, so in
# LIFO execution order it runs AFTER rate limiting. Previously the expensive
# body-buffering scan ran first on every request, including ones a cheap
# 429 would have rejected immediately.
app.add_middleware(AttackDetectionMiddleware)  # Detect SQLi, XSS, path traversal
app.add_middleware(TenantMiddleware)  # Tenant context for multi-tenant features
# P1-15: RateLimit added AFTER Tenant so it runs FIRST on the request path
# (LIFO) — 429s reject before the tenant lookup, not after it.
app.add_middleware(RateLimitMiddleware)  # User/tier-based rate limiting
app.add_middleware(SecurityHeadersMiddleware)  # XSS prevention headers
app.add_middleware(
    RequestBodyLimitMiddleware, max_body_size=settings.app.MAX_BODY_SIZE
)  # Configurable limit (runs AFTER AttackDetection in LIFO)
app.add_middleware(IPFilterMiddleware)  # Block unwanted IPs, sets ip_whitelisted state
app.add_middleware(QueryProfilerMiddleware)  # Dev-mode query profiling (disabled in production)
# Setup GZip with a content-type filter (exempts SSE and binary)
# P2-7: compresslevel 4 over zlib's default 6 — roughly 2x faster compression
# for a small size penalty; the right trade for a JSON API serving many small
# bodies where CPU per request matters more than bytes on the wire.
app.add_middleware(GZipFilterMiddleware, minimum_size=500, compresslevel=4)

# Admin mounting moved to lifespan


# OpenAPI custom schema (single wiring point in api/openapi.py)
from src.app.api.openapi import setup_custom_openapi

setup_custom_openapi(app)
