"""P2-7: shared middleware exempt-path set.

Two of the three exempt-path sets (attack_detection, ip_filter) have drifted
before; this module owns the single source of truth for the *common* set.
Per-middleware modules may extend with their own additional paths but must
not redefine the common ones.

The constants are frozensets so consumers can check ``path in COMMON_EXEMPT_PATHS``
in O(1) and union cheaply with their own.
"""

from __future__ import annotations

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
        "/live",
        "/metrics",
        # S8 FIX: webhook endpoints receive third-party payloads that may
        # contain SQL-like strings. The receiving endpoints verify signatures
        # independently (e.g. Stripe HMAC).
        "/api/v1/billing/webhook",
        "/api/v1/webhooks/stripe",
    }
)


def is_path_exempt(path: str, additional: frozenset[str] = frozenset()) -> bool:
    """Return True if path is in the common set or the middleware's extras.

    Per-middleware modules call this with their own extra paths; the
    common set is checked once.
    """
    if path in COMMON_EXEMPT_PATHS or path in additional:
        return True
    # Common case: sub-paths of an exempt prefix (e.g. /metrics/foo).
    # Mirrors the existing prefix-startswith checks in the three middlewares.
    for prefix in COMMON_EXEMPT_PATHS:
        if path.startswith(f"{prefix}/"):
            return True
    for prefix in additional:
        if path.startswith(f"{prefix}/"):
            return True
    return False