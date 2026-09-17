"""Tier entitlement seam — the ONE capability + limit resolution interface.

The docs (``tiers/AGENTS.md``) claim tiers define tenant capabilities and
resource limits, but the ``Tier`` model holds only a name while each
consumer encoded its own limits (webhook test 10/60, email 100/1000,
storage schema defaults). This module is the seam the docs describe:

- :class:`TierName` — canonical tier identifiers (``free``/``basic`` /
  ``pro`` / ``enterprise``, matching the ``TIER_RATE_LIMITS`` settings
  keys; the free default mirrors
  ``settings.billing.DEFAULT_FREE_TIER_NAME``).
- :class:`Entitlement` — frozen value object carrying every non-HTTP
  limit (webhook test calls, email quotas, storage-quota defaults).
  HTTP path limits stay tier-row-driven via ``rate_policy`` /
  ``get_tier_policy_limits``; this module owns the *fallback/default*
  layer those paths consult.
- :func:`resolve_entitlement` — pure sync lookup by tier name
  (unknown/``None`` → default; every tier maps to today's effective
  values, so adopting the seam changes no behavior — per-tier tuning
  becomes a data change here instead of a code change per consumer).
- :func:`resolve_path_limit` — tier-indexed ``RateLimit``-row lookup
  with entitlement-default fallback (the query webhooks duplicated
  inline without the policy cache).

Import-order note: :class:`RateLimit` is imported at module top. This is
cycle-safe — ``rate_limits/models.py`` depends only on
``tiers/models.py`` (leaf), never on this module — but this module must
stay service-free (never import ``tiers.service`` / ``rate_limits.service``;
both compose *this* direction is forbidden by the facades' lazy guards).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.logger import get_logger
from src.app.modules.rate_limits.models import RateLimit
from src.app.modules.tiers.models import TierName

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Entitlement:
    """Non-HTTP limits for one tier (frozen value object).

    Field values are today's effective behavior, centralized — not new
    policy. Webhook-test (10/60) mirrors the webhooks fallback;
    email quotas (100/min, 1000/hour) mirror the notifications P2-7
    constants; storage defaults (10000 files, 10 GiB) mirror the
    storage-settings schema defaults for new tenant settings rows.
    """

    webhook_test_limit: int = 10
    webhook_test_period: int = 60
    emails_per_minute: int = 100
    email_minute_window_seconds: int = 60
    emails_per_hour: int = 1000
    email_hour_window_seconds: int = 3600
    storage_file_count: int = 10000
    storage_quota_bytes: int = 10 * 1024 * 1024 * 1024


DEFAULT_ENTITLEMENT = Entitlement()

# Per-tier table. Uniform today (current behavior is tier-agnostic for
# every limit owned here) — differentiating a tier is a data change in
# this table, not a code change in each consumer.
ENTITLEMENTS: dict[str, Entitlement] = {
    TierName.FREE.value: DEFAULT_ENTITLEMENT,
    TierName.BASIC.value: DEFAULT_ENTITLEMENT,
    TierName.PRO.value: DEFAULT_ENTITLEMENT,
    TierName.ENTERPRISE.value: DEFAULT_ENTITLEMENT,
}


def resolve_entitlement(tier_name: str | None) -> Entitlement:
    """Return the entitlement for a tier name (case-insensitive).

    Unknown, blank, or ``None`` names resolve to :data:`DEFAULT_ENTITLEMENT`
    — every quota surface stays fail-open with today's values instead of
    raising on tenants whose tier row is missing or renamed.
    """
    if tier_name:
        normalized = tier_name.strip().lower()
        entitlement = ENTITLEMENTS.get(normalized)
        if entitlement is not None:
            return entitlement
    return DEFAULT_ENTITLEMENT


async def resolve_path_limit(
    db: AsyncSession,
    tier_id: int | None,
    path: str,
    *,
    default: tuple[int, int] | None = None,
) -> tuple[int, int]:
    """Resolve ``(limit, period)`` for one tier-indexed path.

    A matching ``RateLimit`` row wins; otherwise ``default`` (the
    entitlement webhook-test default when omitted). ``tier_id=None``
    (principal without a tier) skips the lookup and returns the default —
    same fail-open posture as the previous inline copies.
    """
    if default is None:
        default = (DEFAULT_ENTITLEMENT.webhook_test_limit, DEFAULT_ENTITLEMENT.webhook_test_period)
    if tier_id is None:
        return default
    stmt = select(RateLimit).where(
        RateLimit.tier_id == tier_id,
        RateLimit.path == path,
    )
    result = await db.execute(stmt)
    row: Any = result.scalar_one_or_none()
    if row is None:
        return default
    return (row.limit, row.period)


__all__ = [
    "DEFAULT_ENTITLEMENT",
    "ENTITLEMENTS",
    "Entitlement",
    "TierName",
    "resolve_entitlement",
    "resolve_path_limit",
]
