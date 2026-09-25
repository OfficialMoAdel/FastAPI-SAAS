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
        patch("src.app.core.redis_provider.get_redis", return_value=redis),
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
        patch("src.app.core.redis_provider.get_redis", return_value=redis),
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
        patch("src.app.core.redis_provider.get_redis", return_value=redis),
        patch.object(FeatureFlagService, "get_flag_by_key_or_none", AsyncMock(return_value=None)),
    ):
        assert await service.is_enabled(tenant_id, "beta") is True


def _override_raw(custom_value):
    """An override whose custom_value is exactly the given shape."""
    override = MagicMock()
    override.custom_value = custom_value
    return override


# The four custom_value shapes the shared walk must resolve identically, plus
# the two "no override value" cases. Each is exercised through BOTH public
# entry points, because before the shared helper the typed and boolean paths
# carried their own copy of this walk and could drift.
OVERRIDE_SHAPES = [
    # A non-boolean value is returned raw by the typed path and reads as
    # False through is_enabled, which never coerces arbitrary strings.
    pytest.param({"value": "purple"}, "purple", False, id="dict-value"),
    pytest.param({"enabled": True}, True, True, id="dict-enabled"),
    pytest.param({"enabled": False}, False, False, id="dict-enabled-false"),
    pytest.param("raw-string", "raw-string", False, id="bare-string"),
    pytest.param(7, 7, False, id="bare-int"),
    pytest.param({"unrelated": 1}, {"unrelated": 1}, False, id="dict-without-known-keys"),
    # Both keys present: "value" wins. This is the one shape where the typed
    # and boolean copies of the walk could historically disagree, so it is
    # pinned explicitly rather than left to whichever key is checked first.
    pytest.param({"value": "purple", "enabled": True}, "purple", False, id="dict-both-keys"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("custom_value", "typed_expected", "bool_expected"), OVERRIDE_SHAPES)
async def test_both_entry_points_agree_on_every_override_shape(
    custom_value, typed_expected, bool_expected
):
    """get_flag_value and is_enabled walk the override the same way.

    They differ only at the leaf — one returns the raw value, the other
    applies parse_flag_bool. The walk itself (which key wins, and when the
    registry default applies) is owned by one helper, so the two cannot
    disagree about precedence.
    """
    tenant_id = uuid4()
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.setex = AsyncMock()
    db = AsyncMock()
    db.get = AsyncMock(return_value=_tenant({}))

    service = FeatureFlagService(db=db)
    with (
        patch("src.app.core.redis_provider.get_redis", return_value=redis),
        patch.object(FeatureFlagService, "get_flag_by_key_or_none", AsyncMock(return_value=_flag(True))),
        patch.object(
            FeatureFlagService, "get_override", AsyncMock(return_value=_override_raw(custom_value))
        ),
        patch.object(FeatureFlagService, "get_tenant_flags", AsyncMock(return_value={})),
    ):
        assert await service.get_flag_value(tenant_id, "beta") == typed_expected
        assert await service.is_enabled(tenant_id, "beta") is bool_expected


@pytest.mark.asyncio
@pytest.mark.parametrize("override_value", [None, MagicMock(custom_value=None)])
async def test_no_override_value_falls_back_to_the_registry_default(override_value):
    """An override row that supplies no value must not beat the registry default."""
    tenant_id = uuid4()
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.setex = AsyncMock()
    db = AsyncMock()
    db.get = AsyncMock(return_value=_tenant({"beta": False}))

    service = FeatureFlagService(db=db)
    with (
        patch("src.app.core.redis_provider.get_redis", return_value=redis),
        patch.object(FeatureFlagService, "get_flag_by_key_or_none", AsyncMock(return_value=_flag(True))),
        patch.object(FeatureFlagService, "get_override", AsyncMock(return_value=override_value)),
        patch.object(FeatureFlagService, "get_tenant_flags", AsyncMock(return_value={})),
    ):
        assert await service.get_flag_value(tenant_id, "beta") is True
        assert await service.is_enabled(tenant_id, "beta") is True
