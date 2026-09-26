"""Rate limits module: throughput rules attached to tiers (tiers live in modules.tiers)."""

from typing import Any

from fastcrud import FastCRUD

from src.app.core.security.policies import register_rate_policy_fetcher
from src.app.modules.rate_limits.models import RateLimit

__all__ = ["RateLimit"]


async def _fetch_tier_policy_rows(db: Any, tier_id: int) -> list[Any]:
    """The tier's path-policy rows, for the security policy seam.

    Supplied to ``core.security.policies`` at import so the rate-limit policy
    engine can read these rows without core importing this module (H-5). The
    engine keeps deciding which row applies to a path; only the fetch is here.
    """
    crud = FastCRUD(RateLimit)
    rows = await crud.get_multi(db=db, tier_id=tier_id, return_total_count=False)
    return list(rows.get("data") or []) if isinstance(rows, dict) else []


register_rate_policy_fetcher(_fetch_tier_policy_rows)
