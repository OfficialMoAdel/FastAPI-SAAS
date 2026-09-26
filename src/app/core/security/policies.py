"""Access to tier path-policy rows, supplied by the application seam.

Core resolves a tier's path policy — which ``(limit, period)`` applies to a
path — and falls back to the database when its cache misses. Reading those rows
means reaching the rate-limits feature module, which the H-5 contract forbids
from core, so the *fetch* is registered from outside.

What is registered is a fetcher, not a repository: core owns the path-matching
policy (a rate limit is core's concept, per the Path policy seam in CONTEXT.md),
so it keeps deciding which row applies. Only the row access crosses the seam.

A fetcher rather than a CRUD handle for two reasons: the feature module has no
CRUD of its own to hand over — core was constructing ``FastCRUD(RateLimit)``
inline on every call — and an interface of "give me the rows" is narrower than
one of "here is a data access object".

Same shape as the Principal provider, tenant-model and permission-graph seams.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from src.app.core.config import EnvironmentOption, settings
from src.app.core.logger import get_logger

logger = get_logger(__name__)

# (session, tier_id) -> the tier's policy rows
RatePolicyFetcher = Callable[[Any, int], Awaitable[list[Any]]]

_rate_policy_fetcher: RatePolicyFetcher | None = None


def register_rate_policy_fetcher(fetcher: RatePolicyFetcher) -> None:
    """Register the tier policy fetcher. Called by the application seam."""
    global _rate_policy_fetcher
    _rate_policy_fetcher = fetcher


def reset_rate_policy_fetcher() -> None:
    """Reset the registered fetcher (test isolation).

    TEST-ONLY, blocked in production: a reset there would fail the next cache
    miss at request time rather than the next boot.
    """
    if settings.env.ENVIRONMENT == EnvironmentOption.PRODUCTION:
        raise RuntimeError("reset_rate_policy_fetcher is test-only and blocked in production")
    global _rate_policy_fetcher
    _rate_policy_fetcher = None


def require_rate_policy_fetcher() -> RatePolicyFetcher:
    """The registered fetcher, or a loud failure when the seam is unwired."""
    if _rate_policy_fetcher is None:
        raise RuntimeError(
            "rate policy fetcher is not registered. Import src.app.models (the "
            "single model-registration point) before resolving tier policies."
        )
    return _rate_policy_fetcher


def ensure_rate_policy_fetcher_registered() -> None:
    """Boot gate: raise when the seam is unwired. Call once at startup."""
    if _rate_policy_fetcher is None:
        logger.error("rate_policy_fetcher_unwired")
    require_rate_policy_fetcher()


__all__ = [
    "RatePolicyFetcher",
    "ensure_rate_policy_fetcher_registered",
    "register_rate_policy_fetcher",
    "require_rate_policy_fetcher",
    "reset_rate_policy_fetcher",
]
