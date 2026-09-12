"""Tiers module — canonical home of the Tier model, schemas, and service.

P1-18: single ownership complete. The rate-limits rule layer remains in
``modules/rate_limits`` (which composes ``TierService`` for tier-shaped
operations).

Import-order note: importing any ``tiers.*`` submodule executes this
package __init__ first, so it must NOT eagerly import ``tiers/router``
— the router needs ``src.app.api.deps`` (which transitively imports
``users.service`` → ``tiers.models``). ``get_tier_service`` therefore
stays lazy; everything else imports directly (verified cycle-free in
both tiers-first and rate_limits-first orders).
"""

from typing import Any

from src.app.modules.tiers.models import Tier
from src.app.modules.tiers.schemas import (
    TierBase,
    TierCreate,
    TierCreateInternal,
    TierDelete,
    TierRead,
    TierUpdate,
    TierUpdateInternal,
)


def __getattr__(name: str) -> Any:
    """Lazy service/router access (cycle guard).

    FIX (code-review): eager ``from .service import TierService`` created a
    hard cycle — rate_limits.models → tiers.models executes this __init__ →
    tiers.service → rate_limits.models (partially initialized) → ImportError
    when anything imported main.py cold (subprocess/import e2e). Models and
    schemas stay eager (they are leaves); the service (which imports
    rate_limits) resolves lazily like the router already does.
    """
    if name == "TierService":
        from src.app.modules.tiers.service import TierService

        return TierService
    if name == "get_tier_service":
        from src.app.modules.tiers.router import get_tier_service

        return get_tier_service
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "Tier",
    "TierBase",
    "TierCreate",
    "TierCreateInternal",
    "TierDelete",
    "TierRead",
    "TierService",
    "TierUpdate",
    "TierUpdateInternal",
    "get_tier_service",
]
