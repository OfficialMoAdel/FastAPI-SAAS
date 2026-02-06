"""Unit tests for parallel passkey credential decryption (P1-1 optimization).

Tests verify that credential decryption uses thread pool parallelization
for improved performance with multiple passkeys.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from webauthn.helpers.structs import PublicKeyCredentialDescriptor, PublicKeyCredentialType

from src.app.modules.passkey.service import PasskeyService


@pytest.fixture
def mock_db():
    """Mock database session."""
    return AsyncMock()


@pytest.fixture
def passkey_service(mock_db):
    """PasskeyService instance with mocked database."""
    return PasskeyService()


@pytest.fixture
def mock_credentials():
    """Mock passkey credentials."""
    creds = []
    for i in range(5):
        cred = MagicMock()
        cred.credential_id = f"encrypted_cred_{i}".encode()
        cred.transports = ["usb", "nfc"]
        creds.append(cred)
    return creds


class TestParallelDecryption:
    """Test parallel credential decryption."""

    @pytest.mark.asyncio
    @patch("src.app.modules.passkey.service.crud_passkey_credentials.get_by_user")
    @patch("src.app.core.security.encryption.get_encryption")
    @patch("src.app.modules.passkey.service.asyncio.to_thread")
    @patch("src.app.core.config.settings")
    async def test_decryption_uses_thread_pool(
        self,
        mock_settings,
        mock_to_thread,
        mock_get_encryption,
        mock_get_by_user,
        passkey_service,
        mock_db,
        mock_credentials,
    ):
        """Verify asyncio.to_thread is called for each credential decryption."""
        # Arrange - disable optimized path to test the crud_passkey_credentials code path
        mock_settings.PASSKEY_USE_COLUMN_SELECT = False
        mock_settings.PASSKEY_MAX_CREDENTIALS_PER_USER = 100
        mock_settings.PASSKEY_DECRYPT_CONCURRENCY_LIMIT = 10

        mock_get_by_user.return_value = mock_credentials
        mock_encryption = MagicMock()
        mock_get_encryption.return_value = mock_encryption

        # Mock to_thread to return decrypted values
        async def mock_decrypt(func, cred_id):
            return b"decrypted_" + cred_id

        mock_to_thread.side_effect = mock_decrypt

        # Act
        result = await passkey_service._get_existing_credentials(mock_db, user_id=123)

        # Assert
        assert len(result) == 5
        assert mock_to_thread.call_count == 5, "Should call to_thread for each credential"

        # Verify each call used thread pool
        for call in mock_to_thread.call_args_list:
            args = call[0]
            assert args[0] == mock_encryption.decrypt_credential
            assert isinstance(args[1], bytes)

    @pytest.mark.asyncio
    @patch("src.app.modules.passkey.service.crud_passkey_credentials.get_by_user")
    @patch("src.app.core.security.encryption.get_encryption")
    @patch("src.app.core.config.settings")
    async def test_parallel_decryption_faster(
        self,
        mock_settings,
        mock_get_encryption,
        mock_get_by_user,
        passkey_service,
        mock_db,
        mock_credentials,
    ):
        """Verify parallel decryption is faster than sequential for 5 credentials."""
        # Arrange - disable optimized path
        mock_settings.PASSKEY_USE_COLUMN_SELECT = False
        mock_settings.PASSKEY_MAX_CREDENTIALS_PER_USER = 100
        mock_settings.PASSKEY_DECRYPT_CONCURRENCY_LIMIT = 10

        mock_get_by_user.return_value = mock_credentials
        mock_encryption = MagicMock()
        mock_get_encryption.return_value = mock_encryption

        # Simulate 10ms decryption time per credential
        def slow_decrypt(cred_id):
            time.sleep(0.01)  # 10ms
            return b"decrypted_" + cred_id

        mock_encryption.decrypt_credential = slow_decrypt

        # Act - measure parallel execution time
        start = time.perf_counter()
        result = await passkey_service._get_existing_credentials(mock_db, user_id=123)
        parallel_time = time.perf_counter() - start

        # Assert
        assert len(result) == 5
        # Parallel should complete in ~10ms (max of concurrent operations)
        # Sequential would take ~50ms (5 x 10ms)
        # Allow some overhead, but should be significantly faster than sequential
        assert parallel_time < 0.03, f"Parallel took {parallel_time:.3f}s, expected <0.03s"

        # Verify it's faster than sequential would be
        sequential_time = 5 * 0.01  # 5 credentials x 10ms each
        speedup = sequential_time / parallel_time
        assert speedup > 1.5, f"Speedup {speedup:.1f}x should be >1.5x"

    @pytest.mark.asyncio
    @patch("src.app.modules.passkey.service.crud_passkey_credentials.get_by_user")
    @patch("src.app.core.security.encryption.get_encryption")
    @patch("src.app.core.config.settings")
    async def test_decryption_results_unchanged(
        self,
        mock_settings,
        mock_get_encryption,
        mock_get_by_user,
        passkey_service,
        mock_db,
        mock_credentials,
    ):
        """Verify parallel decryption produces same results as sequential."""
        # Arrange - disable optimized path
        mock_settings.PASSKEY_USE_COLUMN_SELECT = False
        mock_settings.PASSKEY_MAX_CREDENTIALS_PER_USER = 100
        mock_settings.PASSKEY_DECRYPT_CONCURRENCY_LIMIT = 10

        mock_get_by_user.return_value = mock_credentials
        mock_encryption = MagicMock()
        mock_get_encryption.return_value = mock_encryption

        # Mock decryption to return predictable values
        def decrypt(cred_id):
            return b"decrypted_" + cred_id

        mock_encryption.decrypt_credential = decrypt

        # Act
        result = await passkey_service._get_existing_credentials(mock_db, user_id=123)

        # Assert - verify all credentials decrypted correctly
        assert len(result) == 5
        for i, descriptor in enumerate(result):
            assert isinstance(descriptor, PublicKeyCredentialDescriptor)
            assert descriptor.type == PublicKeyCredentialType.PUBLIC_KEY
            assert descriptor.id == b"decrypted_encrypted_cred_" + str(i).encode()

    @pytest.mark.asyncio
    @patch("src.app.modules.passkey.service.crud_passkey_credentials.get_by_user")
    @patch("src.app.core.security.encryption.get_encryption")
    @patch("src.app.core.config.settings")
    async def test_error_handling_one_fails(
        self,
        mock_settings,
        mock_get_encryption,
        mock_get_by_user,
        passkey_service,
        mock_db,
        mock_credentials,
    ):
        """Verify one failed decryption doesn't break others."""
        # Arrange - disable optimized path
        mock_settings.PASSKEY_USE_COLUMN_SELECT = False
        mock_settings.PASSKEY_MAX_CREDENTIALS_PER_USER = 100
        mock_settings.PASSKEY_DECRYPT_CONCURRENCY_LIMIT = 10

        mock_get_by_user.return_value = mock_credentials
        mock_encryption = MagicMock()
        mock_get_encryption.return_value = mock_encryption

        # Mock decryption to fail for credential 2
        def decrypt_with_failure(cred_id):
            if b"cred_2" in cred_id:
                raise ValueError("Decryption failed")
            return b"decrypted_" + cred_id

        mock_encryption.decrypt_credential = decrypt_with_failure

        # Act
        result = await passkey_service._get_existing_credentials(mock_db, user_id=123)

        # Assert - should have 4 successful decryptions (skipped the failed one)
        assert len(result) == 4
        # Verify the failed credential is not in results
        for descriptor in result:
            assert b"cred_2" not in descriptor.id

    @pytest.mark.asyncio
    @patch("src.app.modules.passkey.service.crud_passkey_credentials.get_by_user")
    @patch("src.app.core.config.settings")
    async def test_empty_credentials_handled(
        self,
        mock_settings,
        mock_get_by_user,
        passkey_service,
        mock_db,
    ):
        """Verify empty credential list is handled gracefully."""
        # Arrange - disable optimized path
        mock_settings.PASSKEY_USE_COLUMN_SELECT = False
        mock_settings.PASSKEY_MAX_CREDENTIALS_PER_USER = 100
        mock_settings.PASSKEY_DECRYPT_CONCURRENCY_LIMIT = 10

        mock_get_by_user.return_value = []

        # Act
        result = await passkey_service._get_existing_credentials(mock_db, user_id=123)

        # Assert
        assert result == []

    @pytest.mark.asyncio
    @patch("src.app.modules.passkey.service.crud_passkey_credentials.get_by_user")
    @patch("src.app.core.security.encryption.get_encryption")
    @patch("src.app.core.config.settings")
    async def test_event_loop_not_blocked(
        self,
        mock_settings,
        mock_get_encryption,
        mock_get_by_user,
        passkey_service,
        mock_db,
        mock_credentials,
    ):
        """Verify event loop can process other tasks during decryption."""
        # Arrange - disable optimized path
        mock_settings.PASSKEY_USE_COLUMN_SELECT = False
        mock_settings.PASSKEY_MAX_CREDENTIALS_PER_USER = 100
        mock_settings.PASSKEY_DECRYPT_CONCURRENCY_LIMIT = 10

        mock_get_by_user.return_value = mock_credentials
        mock_encryption = MagicMock()
        mock_get_encryption.return_value = mock_encryption

        # Simulate slow decryption (20ms per credential)
        def slow_decrypt(cred_id):
            time.sleep(0.02)
            return b"decrypted_" + cred_id

        mock_encryption.decrypt_credential = slow_decrypt

        # Create a concurrent task that should complete during decryption
        concurrent_task_completed = False

        async def concurrent_task():
            nonlocal concurrent_task_completed
            await asyncio.sleep(0.01)  # 10ms
            concurrent_task_completed = True

        # Act - run decryption and concurrent task together
        decryption_task = passkey_service._get_existing_credentials(mock_db, user_id=123)
        other_task = concurrent_task()

        results = await asyncio.gather(decryption_task, other_task)

        # Assert
        assert len(results[0]) == 5, "Decryption should complete"
        assert concurrent_task_completed, "Concurrent task should complete during decryption"


class TestParallelDecryptionForEmail:
    """Test parallel decryption in _get_credentials_for_email."""

    @pytest.mark.asyncio
    @patch("src.app.modules.users.crud.crud_users.get")
    @patch("src.app.modules.passkey.service.crud_passkey_credentials.get_by_user")
    @patch("src.app.core.security.encryption.get_encryption")
    @patch("src.app.modules.passkey.service.asyncio.to_thread")
    @patch("src.app.core.config.settings")
    async def test_email_decryption_uses_thread_pool(
        self,
        mock_settings,
        mock_to_thread,
        mock_get_encryption,
        mock_get_by_user,
        mock_get_user,
        passkey_service,
        mock_db,
        mock_credentials,
    ):
        """Verify _get_credentials_for_email uses thread pool."""
        # Arrange - disable optimized path
        mock_settings.PASSKEY_USE_COLUMN_SELECT = False
        mock_settings.PASSKEY_MAX_CREDENTIALS_PER_USER = 100
        mock_settings.PASSKEY_DECRYPT_CONCURRENCY_LIMIT = 10

        mock_user = MagicMock()
        mock_user.id = 123
        mock_get_user.return_value = mock_user
        mock_get_by_user.return_value = mock_credentials
        mock_encryption = MagicMock()
        mock_get_encryption.return_value = mock_encryption

        # Mock to_thread to return decrypted values
        async def mock_decrypt(func, cred_id):
            return b"decrypted_" + cred_id

        mock_to_thread.side_effect = mock_decrypt

        # Act
        result = await passkey_service._get_credentials_for_email(mock_db, email="test@example.com")

        # Assert
        assert len(result) == 5
        assert mock_to_thread.call_count == 5, "Should call to_thread for each credential"

        # Verify transports are preserved
        for descriptor in result:
            assert descriptor.transports == ["usb", "nfc"]

    @pytest.mark.asyncio
    @patch("src.app.modules.users.crud.crud_users.get")
    async def test_email_not_found_returns_empty(
        self,
        mock_get_user,
        passkey_service,
        mock_db,
    ):
        """Verify empty list returned when user not found."""
        # Arrange
        mock_get_user.return_value = None

        # Act
        result = await passkey_service._get_credentials_for_email(mock_db, email="notfound@example.com")

        # Assert
        assert result == []
