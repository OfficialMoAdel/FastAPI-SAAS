"""Tiers module (composed over rate_limits; re-exports for backward compatibility).

A-M2: the Tier model now lives here canonically. Q-M6: TierService
composes RateLimitService (no inheritance). The rate-limits rule layer
remains in ``modules/rate_limits``.

Import-order note: ``rate_limits/models.py`` re-imports ``tiers/models``
(the deprecated Tier re-export), so this package __init__ must NOT eagerly
import ``tiers/service.py`` — the service reaches back into
``rate_limits/models.py`` for RateLimit and would close a circular import
when a caller imports rate_limits first. The service is therefore resolved
lazily via __getattr__ (mirrors the storage module's lazy-service pattern).
"""

from typing import Any

from src.app.modules.tiers.models import Tier
from src.app.modules.tiers.schemas import Tier as TierSchema
from src.app.modules.tiers.schemas import (
    TierBase,
    TierCreate,
    TierCreateInternal,
    TierDelete,
    TierRead,
    TierUpdate,
    TierUpdateInternal,
)

__all__ = [
    "Tier",
    "TierBase",
    "TierCreate",
    "TierCreateInternal",
    "TierDelete",
    "TierRead",
    "TierSchema",
    "TierService",
    "TierUpdate",
    "TierUpdateInternal",
    "get_tier_service",
]


def __getattr__(name: str) -> Any:
    """Lazily resolve the service exports to keep model imports cycle-free."""
    if name == "TierService":
        from src.app.modules.tiers import service as _service

        return _service.TierService
    if name == "get_tier_service":
        from src.app.modules.tiers import router as _router

        return _router.get_tier_service
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
