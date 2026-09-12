"""P2-7: shared middleware exempt-path set.

Two of the three exempt-path sets (attack_detection, ip_filter) have drifted
before; this module owns the single source of truth for the *common* set.
Per-middleware modules may extend with their own additional paths but must
not redefine the common ones.

The constants are frozensets so consumers can check ``path in COMMON_EXEMPT_PATHS``
in O(1) and union cheaply with their own.
"""

from __future__ import annotations

from functools import lru_cache

# Paths that are universally exempt from attack_detection, ip_filter, and
# tenant resolution: docs/openapi (unauthenticated), health/live/ready
# (orchestrator probes), and the well-known Stripe-style webhook receivers.
COMMON_EXEMPT_PATHS: frozenset[str] = frozenset(
    {
        "/docs",
        "/redoc",
        "/openapi.json",
        # tenant.py and attack_detection.py diverge on whether health lives
        # at /health or /api/v1/health; we accept both.
        "/health",
        "/health/live",
        "/health/ready",
        "/api/v1/health",
        "/api/v1/health/live",
        "/api/v1/health/ready",
        "/api/v1/ready",
        "/ready",
        # H-1: /metrics REMOVED from the common exempt set. The metrics
        # endpoints are superuser-gated (api/monitoring.py) and live under
        # /api/metrics[...]; leaving bare /metrics exempt let anonymous
        # scrapers bypass ip_filter, attack detection, AND tenant middleware.
        # Real metrics paths are NOT exempt (they never were) — this entry
        # only matched stray bare-/metrics probes.
        # S8 FIX: webhook endpoints receive third-party payloads that may
        # contain SQL-like strings. The receiving endpoints verify signatures
        # independently (e.g. Stripe HMAC).
        "/api/v1/billing/webhook",
        "/api/v1/webhooks/stripe",
    }
)


# ip_filter's exempt set: the common paths MINUS the docs/openapi trio. The
# e2e contract (tests/e2e/core/security/test_ip_filtering.py) requires
# ip_filter to keep blacklist + bad-bot enforcement on /docs, /redoc and
# /openapi.json (scanner defense), while health probes and the Stripe-style
# webhook receivers stay exempt (a blacklisted Stripe source IP must never
# get webhooks 403'd). Derived so future additions to the common set are
# inherited automatically — no drift.
IP_FILTER_EXEMPT_PATHS: frozenset[str] = frozenset(
    COMMON_EXEMPT_PATHS - {"/docs", "/redoc", "/openapi.json"}
)


def is_path_exempt(
    path: str,
    additional: frozenset[str] = frozenset(),
    base: frozenset[str] = COMMON_EXEMPT_PATHS,
) -> bool:
    """Return True if path is in the base set or the middleware's extras.

    Per-middleware modules call this with their own extra paths; the
    base set is checked once. Pass a narrower ``base`` (e.g.
    IP_FILTER_EXEMPT_PATHS) to exempt fewer common paths.

    P2 micro: prefix checks run C-speed ``str.startswith(tuple)`` over
    memoized ``prefix + "/"`` tuples (one entry per distinct set — all
    in-repo callers pass module-level frozensets), replacing the
    per-request f-string + Python loop.
    """
    if path in base or path in additional:
        return True
    # Common case: sub-paths of an exempt prefix (e.g. /metrics/foo).
    # Mirrors the existing prefix-startswith checks in the three middlewares.
    if path.startswith(_prefix_tuple(base)):
        return True
    return bool(additional) and path.startswith(_prefix_tuple(additional))


@lru_cache(maxsize=16)
def _prefix_tuple(paths: frozenset[str]) -> tuple[str, ...]:
    """Memoized ``prefix + "/"`` tuple for C-speed startswith checks."""
    return tuple(f"{prefix}/" for prefix in paths)
