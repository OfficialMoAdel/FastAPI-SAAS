"""Unit tests for Candidate 12: Deepen Feature Flag Evaluation."""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.app.modules.feature_flags.models import FeatureFlag, FeatureFlagOverride
from src.app.modules.feature_flags.service import FeatureFlagService
from src.app.modules.tenants.models import Tenant


def test_tenant_model_feature_flag_methods():
    """Verify Tenant model helper methods encapsulate JSONB manipulation."""
    tenant = Tenant(name="Test Tenant", slug="test-tenant")
    tenant.feature_flags = {}

    with patch("sqlalchemy.orm.attributes.flag_modified") as mock_modified:
        tenant.set_feature_flag("beta_mode", True)
        assert tenant.feature_flags["beta_mode"] is True
        mock_modified.assert_called_once_with(tenant, "feature_flags")

    with patch("sqlalchemy.orm.attributes.flag_modified") as mock_modified:
        tenant.update_feature_flags({"flag_a": 1, "flag_b": "on"})
        assert tenant.feature_flags["flag_a"] == 1
        assert tenant.feature_flags["flag_b"] == "on"
        mock_modified.assert_called_once_with(tenant, "feature_flags")

    with patch("sqlalchemy.orm.attributes.flag_modified") as mock_modified:
        removed = tenant.remove_feature_flag("beta_mode")
        assert removed is True
        assert "beta_mode" not in tenant.feature_flags
        mock_modified.assert_called_once_with(tenant, "feature_flags")

        # Nonexistent flag returns False
        mock_modified.reset_mock()
        removed_again = tenant.remove_feature_flag("nonexistent")
        assert removed_again is False
        mock_modified.assert_not_called()


@pytest.mark.asyncio
async def test_is_enabled_precedence_override_wins():
    """Verify tenant override takes precedence over flag default."""
    mock_db = AsyncMock()
    service = FeatureFlagService(mock_db)

    t_id = uuid4()
    flag = FeatureFlag(id=1, key="dark_mode", default_value=False, description="Dark mode toggle")
    override = FeatureFlagOverride(id=1, feature_flag_id=1, tenant_id=t_id, custom_value=True, reason="Pilot program")

    with (
        patch.object(service, "get_flag_by_key_or_none", new=AsyncMock(return_value=flag)),
        patch.object(service, "get_override", new=AsyncMock(return_value=override)),
        patch("src.app.core.redis_provider.get_redis", return_value=None),
    ):
        result = await service.is_enabled(t_id, "dark_mode")

    assert result is True
