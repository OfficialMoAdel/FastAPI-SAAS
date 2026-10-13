"""Request-guard middleware ordering — the single wiring point.

Starlette executes middleware LIFO (last added runs first on request).
This table lists middleware innermost → outermost *registration* order,
i.e. the exact ``add_middleware`` call sequence. ``main.py`` iterates it;
``tests/unit/test_middleware_order.py`` pins the resulting execution
order. Reorder here — never by editing call sites.

Execution order (outermost first on request):
  CORS → RequestTimer → GZip → QueryProfiler → IPFilter → BodyLimit
    → SecurityHeaders → RateLimit → Tenant → AttackDetection → RequestID

Key constraints (do not break):
- CORS outermost (C4): every guard short-circuit (IPFilter 403, RateLimit
  429, AttackDetection 400/403, BodyLimit 413) must still carry
  ``Access-Control-Allow-Origin``, or browsers surface an opaque network
  error and the client cannot read the real status. Registered last ⇒ runs
  first ⇒ wraps every guard rejection.
- RateLimit before AttackDetection: cheap 429 skips the expensive
  body-buffering scan (R4).
- RateLimit before Tenant: 429s skip the tenant lookup (P1-15).
- AttackDetection after BodyLimit on request: 413 rejects oversized
  payloads before the scan buffers them.
- IPFilter early: sets ``ip_whitelisted`` state for RateLimit.
- RequestID innermost: accurate tracing. GZip outermost: compresses
  all responses including 4xx/5xx.
- RequestTimer inside CORS, outside every guard (T-3): it must measure the
  whole server-side span or the client/server gap it exists to expose is
  measured with part of the server missing. It self-excludes structurally
  (t0 is read then handed downstream; the header is written on the way
  back), and is DISABLED by default so that 4.6's "every addition is
  cost-free when disabled" holds - enabling it is a deliberate act.
"""

from __future__ import annotations

from typing import Any


def _request_timer_enabled() -> bool:
    """Whether T-3's server-side timer should run, read from the environment.

    A measurement run sets ``REQUEST_TIMER_ENABLED=1``; everything else
    leaves it unset and the middleware returns immediately. Read here rather
    than hardcoding a value so that MOUNTING the timer and ENABLING it stay
    separate decisions - which is the only reason 4.6 can say an addition is
    cost-free when disabled. Hardcoding True would put an unconditional
    header on every production response to satisfy a measurement.
    """
    import os

    return os.environ.get("REQUEST_TIMER_ENABLED", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


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
    # C4: CORS is OUTERMOST (registered last ⇒ runs first) so guard
    # rejections stay readable cross-origin. Skipped when no origins are
    # configured (see resolve_guard_order).
    # T-3 RequestTimer sits immediately inside CORS, so it times every guard
    # and the handler while CORS still wraps it. CORS must stay outermost or a
    # guard short-circuit loses its headers (C4), so the timer cannot go after it.
    #
    # It was ABSENT from this table entirely until now: the class existed, was
    # exported from monitoring/__init__.py and was tested, but nothing added it to
    # the application, so X-Response-Time was structurally unreachable and every
    # "server-side latency" in the Phase-0 dataset had to be client-observed or
    # absent. A verified instrument that nothing calls is the same failure as a
    # missing one, and T-3 closed as a verification bead while this was true.
    _entry(
        "src.app.core.monitoring.request_timer:RequestTimer",
        # Off unless REQUEST_TIMER_ENABLED is set, so 4.6's "every addition is
        # cost-free when disabled" is literally true: the header is absent by
        # default and appears only when a run asks for the paired series.
        # Hardcoding True here would put an unconditional header on every
        # production response to satisfy a measurement.
        enabled=_request_timer_enabled(),
    ),
    _entry("fastapi.middleware.cors:CORSMiddleware"),
)


def resolve_guard_order(settings: Any = None) -> list[tuple[Any, dict[str, Any]]]:
    """Import middleware classes and fill per-entry kwargs.

    Fills the body-limit size and the CORS policy from ``settings``; an
    entry whose configuration is absent (CORS with no origins) is skipped
    rather than registered as a pass-through.
    """
    import importlib

    resolved: list[tuple[Any, dict[str, Any]]] = []
    for cls_path, kwargs in GUARD_ORDER:
        mod_name, cls_name = cls_path.split(":")
        cls = getattr(importlib.import_module(mod_name), cls_name)
        kw = dict(kwargs)
        if cls_name == "RequestBodyLimitMiddleware" and settings is not None:
            kw.setdefault("max_body_size", settings.app.MAX_BODY_SIZE)
        elif cls_name == "CORSMiddleware":
            cors_kwargs = _cors_kwargs(settings)
            if cors_kwargs is None:
                continue
            kw.update(cors_kwargs)
        resolved.append((cls, kw))
    return resolved


def _cors_kwargs(settings: Any) -> dict[str, Any] | None:
    """Build CORSMiddleware kwargs from settings; None when disabled."""
    if settings is None:
        return None
    origins = settings.cors.CORS_ORIGINS
    if not origins:
        return None
    return {
        "allow_origins": origins,
        # Credentialed CORS with a "*" origin is rejected by browsers and
        # defeats the allowlist — mirrors the historical gate in setup.py.
        "allow_credentials": "*" not in origins,
        "allow_methods": settings.cors.CORS_METHODS,
        "allow_headers": settings.cors.CORS_HEADERS,
    }


__all__ = ["GUARD_ORDER", "resolve_guard_order"]
