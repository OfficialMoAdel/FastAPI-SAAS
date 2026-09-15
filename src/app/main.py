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
from src.app.core.guard import resolve_guard_order
from src.app.core.setup import create_application, lifespan_with_admin

app = create_application(
    router=get_router(),
    settings=settings,
    lifespan=lifespan_with_admin,
    default_response_class=ORJSONResponse,
)


# Middleware stack: single wiring point is core/guard/ordering.py
# (GUARD_ORDER, innermost → outermost registration; LIFO execution).
# Request flow: GZip → QueryProfiler → IPFilter → BodyLimit →
# SecurityHeaders → RateLimit → Tenant → AttackDetection → RequestID →
# CORS → Handler. Pinned by tests/unit/test_middleware_order.py (R1-16).
for _mw_cls, _mw_kwargs in resolve_guard_order(settings):
    if _mw_cls.__name__ == "GZipFilterMiddleware":
        # P2-7: compresslevel 4 over zlib's default 6 — roughly 2x faster
        # compression for a small size penalty; the right trade for a JSON
        # API serving many small bodies. Minimum 500 bytes.
        _mw_kwargs = {**_mw_kwargs, "minimum_size": 500, "compresslevel": 4}
    app.add_middleware(_mw_cls, **_mw_kwargs)

# Admin mounting moved to lifespan


# OpenAPI custom schema (single wiring point in api/openapi.py)
from src.app.api.openapi import setup_custom_openapi

setup_custom_openapi(app)
