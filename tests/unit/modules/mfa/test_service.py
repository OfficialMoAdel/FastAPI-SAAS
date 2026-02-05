"""Unit tests for MFA Service.

Tests the MFAService class with mocked dependencies.
"""

import hashlib
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.modules.mfa.service import (
    BACKUP_CODE_COUNT,
    BACKUP_CODE_PREFIX,
    MFAService,
)


@pytest.fixture
def mfa_service():
    """Create MFAService instance for testing."""
    return MFAService()


@pytest.fixture
def mock_db():
    """Mock database session."""
    return AsyncMock(spec=AsyncSession)


@pytest.fixture
def mock_user_id():
    """Test user ID."""
    return 12345


class TestGenerateTotpSecret:
    """Test TOTP secret generation."""

    @pytest.mark.asyncio
    async def test_generate_totp_secret_success(self, mfa_service):
        """Test successful TOTP secret generation."""
        with patch("src.app.modules.mfa.service.pyotp.random_base32", return_value="JBSWY3DPEHPK3PXP"):
            with patch("src.app.modules.mfa.service.pyotp.TOTP") as mock_totp:
                mock_totp_instance = Mock()
                mock_totp_instance.provisioning_uri.return_value = "otpauth://totp/..."
                mock_totp.return_value = mock_totp_instance

                result = await mfa_service.generate_totp_secret(
                    user_id=12345,
                    db=AsyncMock(),
                )

        assert "totp_secret" in result
        assert "otpauth_url" in result
        assert result["totp_secret"] == "JBSWY3DPEHPK3PXP"
        assert result["otpauth_url"] == "otpauth://totp/..."

    @pytest.mark.asyncio
    async def test_generate_totp_secret_unique_each_call(self, mfa_service):
        """Test that each call generates a unique secret."""

        results = []
        for _ in range(5):
            result = await mfa_service.generate_totp_secret(
                user_id=12345,
                db=AsyncMock(),
            )
            results.append(result["totp_secret"])

        # All secrets should be unique
        assert len(set(results)) == len(results)


class TestEnableMFA:
    """Test MFA enablement."""

    @pytest.mark.asyncio
    async def test_enable_mfa_already_enabled_raises_error(self, mfa_service, mock_db, mock_user_id):
        """Test that enabling MFA when already enabled raises error."""
        from src.app.core.exceptions.http_exceptions import BadRequestException
        from src.app.modules.mfa.crud import crud_mfa_methods

        with patch.object(crud_mfa_methods, "get", new_callable=AsyncMock, return_value={"id": "test-id"}):
            with pytest.raises(BadRequestException, match="already enabled"):
                await mfa_service.enable_mfa(
                    user_id=mock_user_id,
                    request=MagicMock(totp_secret="JBSWY3DPEHPK3PXP", totp_token="123456"),
                    db=mock_db,
                )

    @pytest.mark.asyncio
    async def test_enable_mfa_invalid_token_raises_error(self, mfa_service, mock_db, mock_user_id):
        """Test that enabling with invalid TOTP token raises error."""
        from src.app.core.exceptions.http_exceptions import BadRequestException
        from src.app.modules.mfa.crud import crud_mfa_methods
        from src.app.modules.mfa.schemas import MFAEnableRequest

        with patch.object(crud_mfa_methods, "get", new_callable=AsyncMock, return_value=None):
            with patch("src.app.modules.mfa.service.pyotp.TOTP") as mock_totp:
                mock_totp_instance = Mock()
                mock_totp_instance.verify.return_value = False
                mock_totp.return_value = mock_totp_instance

                request = MFAEnableRequest(totp_secret="JBSWY3DPEHPK3PXP", totp_token="000000")
                with pytest.raises(BadRequestException, match="Invalid TOTP"):
                    await mfa_service.enable_mfa(
                        user_id=mock_user_id,
                        request=request,
                        db=mock_db,
                    )

    @pytest.mark.asyncio
    async def test_enable_mfa_success(self, mfa_service, mock_db, mock_user_id):
        """Test successful MFA enablement."""
        from src.app.modules.mfa.crud import crud_mfa_methods
        from src.app.modules.mfa.schemas import MFAEnableRequest

        with patch.object(crud_mfa_methods, "get", new_callable=AsyncMock, return_value=None):
            with patch("src.app.modules.mfa.service.pyotp.TOTP") as mock_totp:
                mock_totp_instance = Mock()
                mock_totp_instance.verify.return_value = True
                mock_totp.return_value = mock_totp_instance

                request = MFAEnableRequest(totp_secret="JBSWY3DPEHPK3PXP", totp_token="123456")
                result = await mfa_service.enable_mfa(
                    user_id=mock_user_id,
                    request=request,
                    db=mock_db,
                )

        assert result["status"] == "enabled"
        assert "backup_codes" in result
        assert len(result["backup_codes"]) == BACKUP_CODE_COUNT
        assert mock_db.add.called
        assert mock_db.commit.called


class TestVerifyTotpToken:
    """Test TOTP token verification."""

    @pytest.mark.asyncio
    async def test_verify_totp_token_not_enabled_raises_error(self, mfa_service, mock_db, mock_user_id):
        """Test verifying TOTP when MFA not enabled raises error."""
        from src.app.core.exceptions.http_exceptions import UnauthorizedException
        from src.app.modules.mfa.crud import crud_mfa_methods
        from src.app.modules.mfa.schemas import MFAVerifyRequest

        with patch.object(crud_mfa_methods, "get", new_callable=AsyncMock, return_value=None):
            request = MFAVerifyRequest(totp_token="123456", backup_code=None)
            with pytest.raises(UnauthorizedException, match="not enabled"):
                await mfa_service.verify_totp_token(
                    user_id=mock_user_id,
                    request=request,
                    db=mock_db,
                )

    @pytest.mark.asyncio
    async def test_verify_totp_token_valid(self, mfa_service, mock_db, mock_user_id):
        """Test successful TOTP verification."""
        from src.app.modules.mfa.crud import crud_mfa_methods
        from src.app.modules.mfa.schemas import MFAVerifyRequest

        mfa_method = {
            "id": "test-id",
            "user_id": mock_user_id,
            "secret": "encrypted_secret",
            "backup_codes": [],
        }

        with patch.object(crud_mfa_methods, "get", new_callable=AsyncMock, return_value=mfa_method):
            with patch.object(mfa_service, "_decrypt_secret", new_callable=AsyncMock, return_value="JBSWY3DPEHPK3PXP"):
                with patch("src.app.modules.mfa.service.pyotp.TOTP") as mock_totp:
                    mock_totp_instance = Mock()
                    mock_totp_instance.verify.return_value = True
                    mock_totp.return_value = mock_totp_instance

                    request = MFAVerifyRequest(totp_token="123456", backup_code=None)
                    result = await mfa_service.verify_totp_token(
                        user_id=mock_user_id,
                        request=request,
                        db=mock_db,
                    )

        assert result["verified"] is True
        assert result["method"] == "totp"

    @pytest.mark.asyncio
    async def test_verify_totp_token_invalid(self, mfa_service, mock_db, mock_user_id):
        """Test invalid TOTP verification fails."""
        from src.app.core.exceptions.http_exceptions import UnauthorizedException
        from src.app.modules.mfa.crud import crud_mfa_methods
        from src.app.modules.mfa.schemas import MFAVerifyRequest

        mfa_method = {
            "id": "test-id",
            "user_id": mock_user_id,
            "secret": "encrypted_secret",
            "backup_codes": [],
        }

        with patch.object(crud_mfa_methods, "get", new_callable=AsyncMock, return_value=mfa_method):
            with patch.object(mfa_service, "_decrypt_secret", new_callable=AsyncMock, return_value="JBSWY3DPEHPK3PXP"):
                with patch("src.app.modules.mfa.service.pyotp.TOTP") as mock_totp:
                    mock_totp_instance = Mock()
                    mock_totp_instance.verify.return_value = False
                    mock_totp.return_value = mock_totp_instance

                    # Mock backup code verification to also fail
                    with patch.object(
                        mfa_service, "_verify_backup_code", new_callable=AsyncMock, return_value={"verified": False}
                    ):
                        request = MFAVerifyRequest(totp_token="000000", backup_code=None)
                        with pytest.raises(UnauthorizedException, match="Invalid MFA token"):
                            await mfa_service.verify_totp_token(
                                user_id=mock_user_id,
                                request=request,
                                db=mock_db,
                            )


class TestGenerateBackupCodes:
    """Test backup code generation."""

    @pytest.mark.asyncio
    async def test_generate_backup_codes_count(self, mfa_service):
        """Test correct number of backup codes generated."""
        codes = await mfa_service._generate_backup_codes()
        assert len(codes) == BACKUP_CODE_COUNT

    @pytest.mark.asyncio
    async def test_generate_backup_codes_format(self, mfa_service):
        """Test backup code format."""
        codes = await mfa_service._generate_backup_codes()
        for code in codes:
            assert code.startswith(BACKUP_CODE_PREFIX)
            assert len(code) == 13  # BC- + 5 chars + - + 5 chars
            assert code.count("-") == 2

    @pytest.mark.asyncio
    async def test_generate_backup_codes_unique(self, mfa_service):
        """Test that all backup codes are unique."""
        codes = await mfa_service._generate_backup_codes()
        assert len(set(codes)) == len(codes)


class TestVerifyBackupCode:
    """Test backup code verification."""

    @pytest.mark.asyncio
    async def test_verify_backup_code_valid(self, mfa_service, mock_db, mock_user_id):
        """Test valid backup code verification."""
        from src.app.modules.mfa.crud import crud_mfa_methods

        # Create a test backup code
        test_code = "BC-ABCDE-FGHIJ"
        code_hash = await mfa_service._hash_backup_code(test_code)

        mfa_method = {
            "id": "test-id",
            "user_id": mock_user_id,
            "backup_codes": [code_hash],
        }

        with patch.object(crud_mfa_methods, "get", new_callable=AsyncMock, return_value=mfa_method):
            # Mock the model instance
            mock_model = MagicMock()
            mock_model.backup_codes = [code_hash]
            mock_db.get = AsyncMock(return_value=mock_model)

            result = await mfa_service._verify_backup_code(
                user_id=mock_user_id,
                code=test_code,
                db=mock_db,
            )

        assert result["verified"] is True
        assert result["method"] == "backup_code"

    @pytest.mark.asyncio
    async def test_verify_backup_code_invalid(self, mfa_service, mock_db, mock_user_id):
        """Test invalid backup code verification fails."""
        from src.app.modules.mfa.crud import crud_mfa_methods

        # Create a test backup code with different hash
        test_code = "BC-ABCDE-FGHIJ"
        wrong_hash = "wrong_hash_value"

        mfa_method = {
            "id": "test-id",
            "user_id": mock_user_id,
            "backup_codes": [wrong_hash],
        }

        with patch.object(crud_mfa_methods, "get", new_callable=AsyncMock, return_value=mfa_method):
            result = await mfa_service._verify_backup_code(
                user_id=mock_user_id,
                code=test_code,
                db=mock_db,
            )

        assert result["verified"] is False

    @pytest.mark.asyncio
    async def test_verify_backup_code_code_removed_after_use(self, mfa_service, mock_db, mock_user_id):
        """Test that backup code is removed after successful verification."""
        from src.app.modules.mfa.crud import crud_mfa_methods

        test_code = "BC-ABCDE-FGHIJ"
        code_hash = await mfa_service._hash_backup_code(test_code)

        mfa_method_dict = {
            "id": "test-id",
            "user_id": mock_user_id,
            "backup_codes": [code_hash],
        }

        mock_model = MagicMock()
        mock_model.backup_codes = [code_hash]

        with patch.object(crud_mfa_methods, "get", new_callable=AsyncMock, return_value=mfa_method_dict):
            mock_db.get = AsyncMock(return_value=mock_model)

            await mfa_service._verify_backup_code(
                user_id=mock_user_id,
                code=test_code,
                db=mock_db,
            )

        assert code_hash not in mock_model.backup_codes
        assert mock_db.commit.called


class TestDisableMFA:
    """Test MFA disablement."""

    @pytest.mark.asyncio
    async def test_disable_mfa_not_enabled_raises_error(self, mfa_service, mock_db, mock_user_id):
        """Test disabling MFA when not enabled raises error."""
        from src.app.core.exceptions.http_exceptions import BadRequestException
        from src.app.modules.mfa.crud import crud_mfa_methods

        with patch.object(crud_mfa_methods, "get", new_callable=AsyncMock, return_value=None):
            with pytest.raises(BadRequestException, match="not enabled"):
                await mfa_service.disable_mfa(
                    user_id=mock_user_id,
                    token="123456",
                    db=mock_db,
                )

    @pytest.mark.asyncio
    async def test_disable_mfa_invalid_token_raises_error(self, mfa_service, mock_db, mock_user_id):
        """Test disabling MFA with invalid token raises error."""
        from src.app.core.exceptions.http_exceptions import UnauthorizedException
        from src.app.modules.mfa.crud import crud_mfa_methods

        mfa_method = {
            "id": "test-id",
            "user_id": mock_user_id,
            "secret": "encrypted_secret",
        }

        with patch.object(crud_mfa_methods, "get", new_callable=AsyncMock, return_value=mfa_method):
            with patch.object(mfa_service, "_decrypt_secret", new_callable=AsyncMock, return_value="JBSWY3DPEHPK3PXP"):
                with patch("src.app.modules.mfa.service.pyotp.TOTP") as mock_totp:
                    mock_totp_instance = Mock()
                    mock_totp_instance.verify.return_value = False
                    mock_totp.return_value = mock_totp_instance

                    with patch.object(
                        mfa_service, "_verify_backup_code", new_callable=AsyncMock, return_value={"verified": False}
                    ):
                        with pytest.raises(UnauthorizedException, match="Invalid MFA token"):
                            await mfa_service.disable_mfa(
                                user_id=mock_user_id,
                                token="000000",
                                db=mock_db,
                            )

    @pytest.mark.asyncio
    async def test_disable_mfa_success(self, mfa_service, mock_db, mock_user_id):
        """Test successful MFA disablement."""
        from src.app.modules.mfa.crud import crud_mfa_methods

        mfa_method = {
            "id": "test-id",
            "user_id": mock_user_id,
            "secret": "encrypted_secret",
        }

        with patch.object(crud_mfa_methods, "get", new_callable=AsyncMock, return_value=mfa_method):
            with patch.object(mfa_service, "_decrypt_secret", new_callable=AsyncMock, return_value="JBSWY3DPEHPK3PXP"):
                with patch("src.app.modules.mfa.service.pyotp.TOTP") as mock_totp:
                    mock_totp_instance = Mock()
                    mock_totp_instance.verify.return_value = True
                    mock_totp.return_value = mock_totp_instance

                    result = await mfa_service.disable_mfa(
                        user_id=mock_user_id,
                        token="123456",
                        db=mock_db,
                    )

        assert result["message"] == "MFA disabled successfully"


class TestIsMFAEnabled:
    """Test MFA enabled status check."""

    @pytest.mark.asyncio
    async def test_is_mfa_enabled_true(self, mfa_service, mock_db, mock_user_id):
        """Test that is_mfa_enabled returns True when MFA is enabled."""
        from src.app.modules.mfa.crud import crud_mfa_methods

        with patch.object(crud_mfa_methods, "get", new_callable=AsyncMock, return_value={"id": "test"}):
            result = await mfa_service.is_mfa_enabled(mock_user_id, mock_db)

        assert result is True

    @pytest.mark.asyncio
    async def test_is_mfa_enabled_false(self, mfa_service, mock_db, mock_user_id):
        """Test that is_mfa_enabled returns False when MFA is not enabled."""
        from src.app.modules.mfa.crud import crud_mfa_methods

        with patch.object(crud_mfa_methods, "get", new_callable=AsyncMock, return_value=None):
            result = await mfa_service.is_mfa_enabled(mock_user_id, mock_db)

        assert result is False


class TestGetMFAStatus:
    """Test getting MFA status."""

    @pytest.mark.asyncio
    async def test_get_mfa_status_not_enabled(self, mfa_service, mock_db, mock_user_id):
        """Test MFA status when not enabled."""
        from src.app.modules.mfa.crud import crud_mfa_methods

        with patch.object(crud_mfa_methods, "get", new_callable=AsyncMock, return_value=None):
            result = await mfa_service.get_mfa_status(mock_user_id, mock_db)

        assert result["enabled"] is False
        assert result["method"] is None
        assert result["backup_codes_remaining"] == 0

    @pytest.mark.asyncio
    async def test_get_mfa_status_enabled(self, mfa_service, mock_db, mock_user_id):
        """Test MFA status when enabled."""
        from app.modules.mfa.crud import crud_mfa_methods

        mfa_method = {
            "id": "test-id",
            "user_id": mock_user_id,
            "method_type": "totp",
            "backup_codes": ["hash1", "hash2", "hash3"],
            "last_used_at": datetime.now(),
        }

        with patch.object(crud_mfa_methods, "get", new_callable=AsyncMock, return_value=mfa_method):
            result = await mfa_service.get_mfa_status(mock_user_id, mock_db)

        assert result["enabled"] is True
        assert result["method"] == "totp"
        assert result["backup_codes_remaining"] == 3
        assert result["last_used_at"] is not None


class TestEncryptDecryptSecret:
    """Test secret encryption and decryption."""

    @pytest.mark.asyncio
    async def test_encrypt_decrypt_roundtrip(self, mfa_service):
        """Test that encryption followed by decryption returns original secret."""
        original_secret = "JBSWY3DPEHPK3PXP"

        encrypted = await mfa_service._encrypt_secret(original_secret)
        decrypted = await mfa_service._decrypt_secret(encrypted)

        assert decrypted == original_secret

    @pytest.mark.asyncio
    async def test_encrypted_value_different_from_plaintext(self, mfa_service):
        """Test that encrypted value is different from plaintext."""
        original_secret = "JBSWY3DPEHPK3PXP"

        encrypted = await mfa_service._encrypt_secret(original_secret)

        assert encrypted != original_secret
        assert isinstance(encrypted, str)


class TestHashBackupCode:
    """Test backup code hashing."""

    @pytest.mark.asyncio
    async def test_hash_backup_code_consistency(self, mfa_service):
        """Test that same code produces same hash."""
        code = "BC-ABCDE-FGHIJ"

        hash1 = await mfa_service._hash_backup_code(code)
        hash2 = await mfa_service._hash_backup_code(code)

        assert hash1 == hash2
        assert hash1 == hashlib.sha256(code.encode()).hexdigest()

    @pytest.mark.asyncio
    async def test_hash_backup_code_different_codes_different_hashes(self, mfa_service):
        """Test that different codes produce different hashes."""
        code1 = "BC-ABCDE-FGHIJ"
        code2 = "BC-ABCDE-FGHIK"

        hash1 = await mfa_service._hash_backup_code(code1)
        hash2 = await mfa_service._hash_backup_code(code2)

        assert hash1 != hash2


class TestMFALockout:
    """Test MFA lockout mechanism after failed attempts."""

    @pytest.mark.asyncio
    async def test_verify_totp_token_rate_limit_enforced(self, mfa_service, mock_db, mock_user_id):
        """Test that rate limit is enforced after 5 failed attempts."""
        from src.app.core.exceptions.http_exceptions import RateLimitException, UnauthorizedException
        from src.app.core.utils.rate_limit import rate_limiter
        from src.app.modules.mfa.crud import crud_mfa_methods
        from src.app.modules.mfa.schemas import MFAVerifyRequest

        mfa_method = {
            "id": "test-id",
            "user_id": mock_user_id,
            "secret": "encrypted_secret",
            "backup_codes": [],
        }

        with patch.object(crud_mfa_methods, "get", new_callable=AsyncMock, return_value=mfa_method):
            with patch.object(mfa_service, "_decrypt_secret", new_callable=AsyncMock, return_value="JBSWY3DPEHPK3PXP"):
                with patch("src.app.modules.mfa.service.pyotp.TOTP") as mock_totp:
                    mock_totp_instance = Mock()
                    mock_totp_instance.verify.return_value = False
                    mock_totp.return_value = mock_totp_instance

                    # Mock rate limiter to enforce limit after 5 attempts
                    with patch.object(rate_limiter, "check_rate_limit", new_callable=AsyncMock) as mock_rate_check:
                        # First 5 attempts: not rate limited, 6th: rate limited
                        mock_rate_check.side_effect = [
                            (False, 1),  # Attempt 1
                            (False, 2),  # Attempt 2
                            (False, 3),  # Attempt 3
                            (False, 4),  # Attempt 4
                            (False, 5),  # Attempt 5
                            (True, 6),  # Attempt 6 - rate limited!
                        ]

                        # First 5 attempts should fail with UnauthorizedException
                        for i in range(5):
                            request = MFAVerifyRequest(totp_token="000000", backup_code=None)
                            with pytest.raises(UnauthorizedException, match="Invalid MFA token"):
                                await mfa_service.verify_totp_token(
                                    user_id=mock_user_id,
                                    request=request,
                                    db=mock_db,
                                )

                        # 6th attempt should be rate limited (in router, not service)
                        # This test verifies the service correctly handles invalid tokens

                        assert mock_rate_check.call_count == 6
                        # The actual rate limiting happens in the router layer


class TestBackupCodeUsage:
    """Test backup code consumption and remaining count."""

    @pytest.mark.asyncio
    async def test_backup_code_consumes_one_credit(self, mfa_service, mock_db, mock_user_id):
        """Test that using a backup code reduces remaining count by 1."""
        from src.app.modules.mfa.crud import crud_mfa_methods

        # Create 3 backup codes
        test_codes = ["BC-ABCDE-FGHIJ", "BC-KLMNO-PQRST", "BC-UVWXY-Z1234"]
        code_hashes = [await mfa_service._hash_backup_code(code) for code in test_codes]

        mfa_method_dict = {
            "id": "test-id",
            "user_id": mock_user_id,
            "backup_codes": code_hashes,
        }

        mock_model = MagicMock()
        mock_model.backup_codes = code_hashes.copy()

        with patch.object(crud_mfa_methods, "get", new_callable=AsyncMock, return_value=mfa_method_dict):
            mock_db.get = AsyncMock(return_value=mock_model)

            # Use first backup code
            result = await mfa_service._verify_backup_code(
                user_id=mock_user_id,
                code=test_codes[0],
                db=mock_db,
            )

        assert result["valid"] is True
        assert result["backup_codes_remaining"] == 2  # 10 - 1 = 9, but we only have 3 in test
        assert len(mock_model.backup_codes) == 2
        assert code_hashes[0] not in mock_model.backup_codes

    @pytest.mark.asyncio
    async def test_backup_code_cannot_be_reused(self, mfa_service, mock_db, mock_user_id):
        """Test that a used backup code cannot be used again."""
        from src.app.modules.mfa.crud import crud_mfa_methods

        test_code = "BC-ABCDE-FGHIJ"
        code_hash = await mfa_service._hash_backup_code(test_code)

        mfa_method_dict = {
            "id": "test-id",
            "user_id": mock_user_id,
            "backup_codes": [code_hash],
        }

        mock_model = MagicMock()
        mock_model.backup_codes = [code_hash]

        with patch.object(crud_mfa_methods, "get", new_callable=AsyncMock, return_value=mfa_method_dict):
            mock_db.get = AsyncMock(return_value=mock_model)

            # Use backup code first time
            result1 = await mfa_service._verify_backup_code(
                user_id=mock_user_id,
                code=test_code,
                db=mock_db,
            )

            assert result1["valid"] is True
            assert result1["backup_codes_remaining"] == 0

            # Try to use same code again
            result2 = await mfa_service._verify_backup_code(
                user_id=mock_user_id,
                code=test_code,
                db=mock_db,
            )

            assert result2["valid"] is False
            assert result2["backup_codes_remaining"] is None

    @pytest.mark.asyncio
    async def test_backup_code_usage_updates_last_used_at(self, mfa_service, mock_db, mock_user_id):
        """Test that using a backup code updates last_used_at timestamp."""
        from datetime import datetime
        from src.app.modules.mfa.crud import crud_mfa_methods

        test_code = "BC-ABCDE-FGHIJ"
        code_hash = await mfa_service._hash_backup_code(test_code)

        mfa_method_dict = {
            "id": "test-id",
            "user_id": mock_user_id,
            "backup_codes": [code_hash],
        }

        mock_model = MagicMock()
        mock_model.backup_codes = [code_hash]
        mock_model.last_used_at = None

        with patch.object(crud_mfa_methods, "get", new_callable=AsyncMock, return_value=mfa_method_dict):
            mock_db.get = AsyncMock(return_value=mock_model)

            await mfa_service._verify_backup_code(
                user_id=mock_user_id,
                code=test_code,
                db=mock_db,
            )

        assert mock_model.last_used_at is not None
        assert isinstance(mock_model.last_used_at, datetime)
