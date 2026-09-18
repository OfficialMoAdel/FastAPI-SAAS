"""Pin the single-store precedence contract for feature flags (B7).

Resolution order (highest wins):
    tenant override (FeatureFlagOverride.custom_value)
    → registry default (FeatureFlag.default_value)
    → legacy tenant JSONB (Tenant.feature_flags)
    → caller default
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.app.modules.feature_flags.service import FeatureFlagService


def _flag(default_value=True):
    flag = MagicMock()
    flag.id = 7
    flag.default_value = default_value
    return flag


def _override(value):
    override = MagicMock()
    override.custom_value = {"value": value}
    return override


def _tenant(flags):
    tenant = MagicMock()
    tenant.feature_flags = flags
    return tenant


@pytest.mark.asyncio
async def test_registry_default_wins_over_tenant_jsonb():
    """A registered flag's default beats a conflicting JSONB entry."""
    tenant_id = uuid4()
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.setex = AsyncMock()
    db = AsyncMock()
    db.get = AsyncMock(return_value=_tenant({"beta": False}))

    service = FeatureFlagService(db=db)
    with (
        patch("src.app.modules.feature_flags.service.get_redis", return_value=redis),
        patch.object(FeatureFlagService, "get_flag_by_key_or_none", AsyncMock(return_value=_flag(True))),
        patch.object(FeatureFlagService, "get_override", AsyncMock(return_value=None)),
    ):
        assert await service.is_enabled(tenant_id, "beta") is True


@pytest.mark.asyncio
async def test_override_wins_over_registry_default():
    """A tenant override beats the registry default."""
    tenant_id = uuid4()
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.setex = AsyncMock()
    db = AsyncMock()
    db.get = AsyncMock(return_value=_tenant({"beta": True}))

    service = FeatureFlagService(db=db)
    with (
        patch("src.app.modules.feature_flags.service.get_redis", return_value=redis),
        patch.object(FeatureFlagService, "get_flag_by_key_or_none", AsyncMock(return_value=_flag(True))),
        patch.object(FeatureFlagService, "get_override", AsyncMock(return_value=_override(False))),
    ):
        assert await service.is_enabled(tenant_id, "beta") is False


@pytest.mark.asyncio
async def test_jsonb_used_only_when_unregistered():
    """Unregistered flags fall back to the legacy JSONB store."""
    tenant_id = uuid4()
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.setex = AsyncMock()
    db = AsyncMock()
    db.get = AsyncMock(return_value=_tenant({"beta": True}))

    service = FeatureFlagService(db=db)
    with (
        patch("src.app.modules.feature_flags.service.get_redis", return_value=redis),
        patch.object(FeatureFlagService, "get_flag_by_key_or_none", AsyncMock(return_value=None)),
    ):
        assert await service.is_enabled(tenant_id, "beta") is True
