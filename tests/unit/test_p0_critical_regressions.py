"""Unit tests for P0 Critical Regressions (P0-1, P0-2, P0-3, P0-4)."""

from unittest.mock import MagicMock, patch

import pytest

from src.app.core.config.environment import EnvironmentOption
from src.app.core.exceptions.http_exceptions import TenantContextError
from src.app.core.utils.ip_utils import is_trusted_proxy
from src.app.middleware.tenant import is_path_exempt
from src.app.modules._base.service import BaseService
from src.app.modules.users.models import User
from src.app.modules.webhooks.signer import verify_signature


class TestP0CriticalRegressions:
    """Regression test suite for P0 Critical fixes."""

    @pytest.mark.asyncio
    async def test_p0_1_get_multi_missing_tenant_fails_closed(self):
        """P0-1: get_multi must raise TenantContextError when tenant context is missing."""
        mock_db = MagicMock()
        service = BaseService[User](model=User, db=mock_db)

        # Ensure no tenant context is active
        with pytest.raises(TenantContextError):
            await service.get_multi()

    def test_p0_2_sso_callback_is_tenant_exempt(self):
        """P0-2: SSO callback paths must be exempt in TenantMiddleware."""
        assert is_path_exempt("/api/v1/account/sso/google/callback") is True
        assert is_path_exempt("/api/v1/account/sso/github/callback") is True
        assert is_path_exempt("/api/v1/account/sso/apple/callback") is True
        assert is_path_exempt("/api/v1/account/sso/microsoft/callback") is True
        assert is_path_exempt("/api/v1/account/sso/google/authorize") is True

    def test_p0_3_webhook_rotation_header_prefix(self):
        """P0-3: Secondary signature must include v1= prefix and be accepted by verify_signature."""
        payload = {"event": "invoice.paid", "amount": 100}
        active_secret = "whsec_active_secret_12345"
        old_secret = "whsec_old_secret_67890"
        timestamp = 1700000000

        from src.app.modules.webhooks.signer import sign_payload

        active_header = sign_payload(payload, active_secret, timestamp)
        old_header = sign_payload(payload, old_secret, timestamp)
        old_v1 = old_header.split("v1=", 1)[1]

        # Correct rotation format with v1= prefix on both signatures
        rotation_header = f"{active_header},v1={old_v1}"

        assert rotation_header.count("v1=") == 2

        # Verify acceptance against both active and old secrets
        with patch("src.app.modules.webhooks.signer.time.time", return_value=timestamp):
            assert verify_signature(payload, active_secret, rotation_header) is True
            assert verify_signature(payload, old_secret, rotation_header) is True

    def test_p0_4_testclient_not_trusted_in_production(self):
        """P0-4: testclient IP must not be trusted in production environment."""
        from src.app.core.config import settings

        with patch.object(settings.env, "ENVIRONMENT", EnvironmentOption.PRODUCTION):
            assert is_trusted_proxy("testclient") is False

        with patch.object(settings.env, "ENVIRONMENT", EnvironmentOption.LOCAL):
            assert is_trusted_proxy("testclient") is True
