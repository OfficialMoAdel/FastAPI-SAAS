"""Unit tests for tiers/entitlement.py (A5 entitlement seam).

Pins the behavior-preserving contract: every tier resolves to today's
effective limits (webhook test 10/60, email 100/min + 1000/hour, storage
defaults 10000 files / 10 GiB), and the path-limit lookup prefers the
tier-indexed RateLimit row with the entitlement default as fallback.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.modules.tiers.entitlement import (
    DEFAULT_ENTITLEMENT,
    Entitlement,
    resolve_entitlement,
    resolve_path_limit,
)
from src.app.modules.tiers.models import TierName


def test_tier_names_match_settings_keys() -> None:
    """Enum values align with TIER_RATE_LIMITS settings keys + free default."""
    assert TierName.FREE.value == "free"
    assert TierName.BASIC.value == "basic"
    assert TierName.PRO.value == "pro"
    assert TierName.ENTERPRISE.value == "enterprise"


def test_resolve_entitlement_known_tiers() -> None:
    """Known tier names resolve (case-insensitive, whitespace-tolerant)."""
    for name in ("free", "Free", " FREE ", "pro", "Pro", "enterprise", "ENTERPRISE", "basic"):
        ent = resolve_entitlement(name)
        assert isinstance(ent, Entitlement)
        assert ent.webhook_test_limit == 10
        assert ent.webhook_test_period == 60


def test_resolve_entitlement_unknown_falls_back() -> None:
    """Unknown, blank, or missing tiers fail open with the default."""
    assert resolve_entitlement("nonexistent") is DEFAULT_ENTITLEMENT
    assert resolve_entitlement("") is DEFAULT_ENTITLEMENT
    assert resolve_entitlement(None) is DEFAULT_ENTITLEMENT


def test_default_entitlement_matches_legacy_literals() -> None:
    """The seam carries the exact values each consumer hard-coded before."""
    assert (DEFAULT_ENTITLEMENT.webhook_test_limit, DEFAULT_ENTITLEMENT.webhook_test_period) == (10, 60)
    assert (DEFAULT_ENTITLEMENT.emails_per_minute, DEFAULT_ENTITLEMENT.email_minute_window_seconds) == (100, 60)
    assert (DEFAULT_ENTITLEMENT.emails_per_hour, DEFAULT_ENTITLEMENT.email_hour_window_seconds) == (1000, 3600)
    assert DEFAULT_ENTITLEMENT.storage_file_count == 10000
    assert DEFAULT_ENTITLEMENT.storage_quota_bytes == 10737418240


def _execute_result(row: object) -> MagicMock:
    """Build the ``db.execute()`` result double returning ``row``."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    return result


@pytest.mark.asyncio
async def test_resolve_path_limit_row_wins() -> None:
    """A matching RateLimit row beats the default."""
    db = AsyncMock()
    db.execute.return_value = _execute_result(SimpleNamespace(limit=5, period=30))

    assert await resolve_path_limit(db, 1, "/api/v1/webhooks/{endpoint_id}/test") == (5, 30)


@pytest.mark.asyncio
async def test_resolve_path_limit_miss_returns_default() -> None:
    """No row → entitlement webhook-test default (10/60)."""
    db = AsyncMock()
    db.execute.return_value = _execute_result(None)

    assert await resolve_path_limit(db, 1, "/api/v1/webhooks/{endpoint_id}/test") == (10, 60)


@pytest.mark.asyncio
async def test_resolve_path_limit_no_tier_skips_db() -> None:
    """tier_id=None returns the default without touching the database."""
    db = AsyncMock()

    assert await resolve_path_limit(db, None, "/api/v1/webhooks/{endpoint_id}/test") == (10, 60)
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_path_limit_custom_default() -> None:
    """Callers may override the fallback default."""
    db = AsyncMock()
    db.execute.return_value = _execute_result(None)

    assert await resolve_path_limit(db, 1, "/some/path", default=(50, 3600)) == (50, 3600)
