"""Request-guard middleware ordering — the single wiring point.

Starlette executes middleware LIFO (last added runs first on request).
This table lists middleware innermost → outermost *registration* order,
i.e. the exact ``add_middleware`` call sequence. ``main.py`` iterates it;
``tests/unit/test_middleware_order.py`` pins the resulting execution
order. Reorder here — never by editing call sites.

Execution order (outermost first on request):
  GZip → QueryProfiler → IPFilter → BodyLimit → SecurityHeaders
    → RateLimit → Tenant → AttackDetection → RequestID → CORS

Key constraints (do not break):
- RateLimit before AttackDetection: cheap 429 skips the expensive
  body-buffering scan (R4).
- RateLimit before Tenant: 429s skip the tenant lookup (P1-15).
- AttackDetection after BodyLimit on request: 413 rejects oversized
  payloads before the scan buffers them.
- IPFilter early: sets ``ip_whitelisted`` state for RateLimit.
- RequestID innermost: accurate tracing. GZip outermost: compresses
  all responses including 4xx/5xx.
"""

from __future__ import annotations

from typing import Any


def _entry(cls_path: str, **kwargs: Any) -> tuple[str, dict[str, Any]]:
    return (cls_path, kwargs)


# Innermost → outermost registration (== add_middleware call order).
GUARD_ORDER: tuple[tuple[str, dict[str, Any]], ...] = (
    _entry("src.app.middleware.request_id:RequestIDMiddleware"),
    _entry("src.app.middleware.attack_detection:AttackDetectionMiddleware"),
    _entry("src.app.middleware.tenant:TenantMiddleware"),
    _entry("src.app.middleware.rate_limit:RateLimitMiddleware"),
    _entry("src.app.middleware.security_headers:SecurityHeadersMiddleware"),
    _entry("src.app.middleware.body_limit:RequestBodyLimitMiddleware"),
    _entry("src.app.middleware.ip_filter:IPFilterMiddleware"),
    _entry("src.app.middleware.query_profiler:QueryProfilerMiddleware"),
    _entry("src.app.middleware.gzip_filter:GZipFilterMiddleware"),
)


def resolve_guard_order(settings: Any = None) -> list[tuple[Any, dict[str, Any]]]:
    """Import middleware classes and fill per-entry kwargs (body-limit size)."""
    import importlib

    resolved: list[tuple[Any, dict[str, Any]]] = []
    for cls_path, kwargs in GUARD_ORDER:
        mod_name, cls_name = cls_path.split(":")
        cls = getattr(importlib.import_module(mod_name), cls_name)
        kw = dict(kwargs)
        if cls_name == "RequestBodyLimitMiddleware" and settings is not None:
            kw.setdefault("max_body_size", settings.app.MAX_BODY_SIZE)
        resolved.append((cls, kw))
    return resolved


__all__ = ["GUARD_ORDER", "resolve_guard_order"]
