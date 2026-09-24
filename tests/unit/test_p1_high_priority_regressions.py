"""Unit tests for P1 High Priority Regressions (P1-1 to P1-6)."""

import time
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError

from src.app.modules.api_keys.service import APIKeyService
from src.app.modules.auth.models import MFAMethod
from src.app.modules.auth.schemas import MFAEnableRequest
from src.app.modules.auth.services.lockout import LoginLockoutPolicy
from src.app.modules.auth.services.mfa import MFAService
from src.app.modules.webhooks.signer import is_timestamp_fresh
from src.app.shared.exceptions import ModelNotFoundError, ValidationError


class TestP1HighPriorityRegressions:
    """Regression test suite for P1 High Priority fixes."""

    @pytest.mark.asyncio
    async def test_p1_1_lockout_policy_is_locked_does_not_increment(self):
        """P1-1: is_locked check must be read-only and not increment attempt counters."""
        lockout = LoginLockoutPolicy()
        mock_redis = AsyncMock()
        mock_redis.get.return_value = None

        is_locked = await lockout.is_locked(mock_redis, "victim@example.com")
        assert is_locked is False
        mock_redis.get.assert_called_once_with("login_attempts:victim@example.com")
        assert mock_redis.eval.call_count == 0

    def test_p1_2_mfa_methods_has_unique_constraint(self):
        """P1-2: MFAMethod model must define unique constraint on (user_id, type)."""
        table_args = getattr(MFAMethod, "__table_args__", ())
        constraint_found = False
        for arg in table_args:
            if hasattr(arg, "columns"):
                col_names = [col.name if hasattr(col, "name") else str(col) for col in arg.columns]
                if "user_id" in col_names and "type" in col_names:
                    constraint_found = True
                    break
        assert constraint_found is True

    @pytest.mark.asyncio
    async def test_p1_2_mfa_service_handles_integrity_error(self):
        """P1-2: enable_mfa must catch IntegrityError and raise ValidationError."""
        service = MFAService()
        mock_db = AsyncMock()
        mock_db.commit.side_effect = IntegrityError("duplicate key", params={}, orig=Exception("unique constraint"))

        with patch("src.app.modules.auth.services.mfa.crud_mfa_methods.get", return_value=None), \
             patch("src.app.modules.auth.services.mfa.get_redis") as mock_get_redis, \
             patch("pyotp.TOTP.verify", return_value=True), \
             patch.object(service, "_encrypt_secret", return_value="enc_secret"), \
             patch.object(service, "_generate_backup_codes", return_value=["c1", "c2"]), \
             patch.object(service, "_hash_backup_code", return_value="hashed_code"), \
             patch("src.app.modules.users.UserDirectory.member", new_callable=AsyncMock) as mock_member:
            mock_member.return_value = MagicMock(tenant_id=uuid.uuid4())
            mock_redis = AsyncMock()
            mock_redis.get.return_value = b"JBSWY3DPEHPK3PXP"
            mock_get_redis.return_value = mock_redis

            req = MFAEnableRequest(setup_token="setup_123", totp_token="123456")
            with pytest.raises(ValidationError) as exc_info:
                await service.enable_mfa(user_id=1, request=req, db=mock_db)

            assert "MFA is already enabled" in str(exc_info.value.detail)
            mock_db.rollback.assert_called_once()

    def test_p1_4_signer_asymmetric_freshness_window(self):
        """P1-4: Signer must reject future timestamps > 60s and stale timestamps > 300s."""
        now = int(time.time())

        # Fresh timestamps
        assert is_timestamp_fresh(now, future_skew_seconds=60) is True
        assert is_timestamp_fresh(now - 100, future_skew_seconds=60) is True
        assert is_timestamp_fresh(now + 30, future_skew_seconds=60) is True

        # Rejected future timestamps (clock skew beyond 60s)
        assert is_timestamp_fresh(now + 90, future_skew_seconds=60) is False

        # Rejected stale timestamps (older than 300s max age)
        assert is_timestamp_fresh(now - 350, future_skew_seconds=60) is False
    @pytest.mark.asyncio
    async def test_p1_5_unban_ip_publishes_invalidation(self):
        """P1-5: unban_ip must publish invalidation to ban:l1_invalidate."""
        from src.app.core.security.auto_ban import unban_ip

        mock_redis = AsyncMock()
        mock_redis.delete.return_value = 1
        with patch("src.app.core.security.auto_ban.get_redis", return_value=mock_redis), \
             patch("src.app.core.security.auto_ban._publish_ban_l1_invalidation", new_callable=AsyncMock) as mock_pub:
            result = await unban_ip("198.51.100.1")
            assert result is True
            mock_pub.assert_called_once_with("198.51.100.1")

    @pytest.mark.asyncio
    async def test_p1_6_rotate_api_key_scopes_query_to_tenant(self):
        """P1-6: rotate_api_key returns 404 for keys belonging to another tenant."""
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        service = APIKeyService(mock_db)
        tenant_a = uuid.uuid4()

        with pytest.raises(ModelNotFoundError):
            await service.rotate_api_key(
                key_id=999,
                user_id=1,
                tenant_id=tenant_a,
                grace_period_days=7,
            )
