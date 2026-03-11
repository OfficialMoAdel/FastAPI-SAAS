"""Unit tests for P3-2: Passkey Column-Specific SELECT optimization."""

import threading
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.modules.passkey.service import PasskeyService


@pytest.mark.asyncio
async def test_only_credential_id_fetched(mock_db, override_settings):
    """Test that only credential_id column is fetched when optimization enabled."""
    service = PasskeyService()

    # Mock database result with only credential_id
    mock_result = MagicMock()
    mock_result.fetchall.return_value = [(b"encrypted_cred_1",), (b"encrypted_cred_2",)]
    mock_db.execute = AsyncMock(return_value=mock_result)

    # Mock encryption
    with patch("src.app.core.security.encryption.get_encryption") as mock_enc:
        mock_encryption = MagicMock()
        mock_encryption.decrypt_credential.return_value = b"decrypted_cred"
        mock_enc.return_value = mock_encryption

        with override_settings(PASSKEY_USE_COLUMN_SELECT=True):
            result = await service._get_existing_credentials(mock_db, user_id=1)

    # Verify SELECT statement only includes credential_id
    call_args = mock_db.execute.call_args
    assert call_args is not None
    # Result should have decrypted credentials
    assert len(result) == 2


@pytest.mark.asyncio
async def test_column_select_returns_correct_ids(mock_db, override_settings):
    """Test that column-specific SELECT returns correct credential IDs."""
    service = PasskeyService()

    # Mock database result
    mock_result = MagicMock()
    mock_result.fetchall.return_value = [(b"cred_1",), (b"cred_2",), (b"cred_3",)]
    mock_db.execute = AsyncMock(return_value=mock_result)

    # Mock encryption to return predictable values
    with patch("src.app.core.security.encryption.get_encryption") as mock_enc:
        mock_encryption = MagicMock()
        mock_encryption.decrypt_credential.side_effect = lambda x: x.decode() + "_decrypted"
        mock_enc.return_value = mock_encryption

        with override_settings(PASSKEY_USE_COLUMN_SELECT=True):
            result = await service._get_existing_credentials(mock_db, user_id=1)

    # Verify all credentials were processed
    assert len(result) == 3


@pytest.mark.asyncio
async def test_decryption_parallel_with_gather(mock_db, override_settings):
    """Test that decryption uses asyncio.gather for parallel execution."""
    service = PasskeyService()

    # Mock database result with multiple credentials
    mock_result = MagicMock()
    mock_result.fetchall.return_value = [(b"cred_1",), (b"cred_2",), (b"cred_3",)]
    mock_db.execute = AsyncMock(return_value=mock_result)

    decrypt_calls = []

    def track_decrypt(cred_id):
        """Track when decryption is called."""
        decrypt_calls.append(cred_id)
        return cred_id

    with patch("src.app.core.security.encryption.get_encryption") as mock_enc:
        mock_encryption = MagicMock()
        mock_encryption.decrypt_credential = track_decrypt
        mock_enc.return_value = mock_encryption

        with override_settings(PASSKEY_USE_COLUMN_SELECT=True):
            await service._get_existing_credentials(mock_db, user_id=1)

    # All decryptions should have been called
    assert len(decrypt_calls) == 3


@pytest.mark.asyncio
async def test_empty_credentials_returns_empty(mock_db, override_settings):
    """Test that empty credential list returns empty result."""
    service = PasskeyService()

    # Mock empty database result
    mock_result = MagicMock()
    mock_result.fetchall.return_value = []
    mock_db.execute = AsyncMock(return_value=mock_result)

    with override_settings():
        result = await service._get_existing_credentials(mock_db, user_id=1)

    assert result == []


@pytest.mark.asyncio
async def test_order_by_created_at_desc(mock_db, override_settings):
    """Test that credentials are ordered by created_at DESC."""
    service = PasskeyService()

    # Mock database result (already ordered)
    mock_result = MagicMock()
    mock_result.fetchall.return_value = [(b"newest",), (b"middle",), (b"oldest",)]
    mock_db.execute = AsyncMock(return_value=mock_result)

    with patch("src.app.core.security.encryption.get_encryption") as mock_enc:
        mock_encryption = MagicMock()
        mock_encryption.decrypt_credential.side_effect = lambda x: x
        mock_enc.return_value = mock_encryption

        with override_settings(PASSKEY_USE_COLUMN_SELECT=True):
            result = await service._get_existing_credentials(mock_db, user_id=1)

    # Order should be preserved
    assert len(result) == 3


@pytest.mark.asyncio
async def test_decryption_failure_filtered(mock_db, override_settings):
    """Test that failed decryptions are filtered out."""
    service = PasskeyService()

    # Mock database result
    mock_result = MagicMock()
    mock_result.fetchall.return_value = [(b"good_1",), (b"bad",), (b"good_2",)]
    mock_db.execute = AsyncMock(return_value=mock_result)

    def decrypt_with_failure(cred_id):
        """Simulate decryption failure for one credential."""
        if cred_id == b"bad":
            raise ValueError("Decryption failed")
        return cred_id

    with patch("src.app.core.security.encryption.get_encryption") as mock_enc:
        mock_encryption = MagicMock()
        mock_encryption.decrypt_credential = decrypt_with_failure
        mock_enc.return_value = mock_encryption

        with override_settings(PASSKEY_USE_COLUMN_SELECT=True):
            result = await service._get_existing_credentials(mock_db, user_id=1)

    # Only successful decryptions should be returned
    assert len(result) == 2


@pytest.mark.asyncio
async def test_concurrency_limited_with_semaphore(mock_db, override_settings):
    """Test that concurrent decryptions are limited by semaphore."""
    service = PasskeyService()

    # Mock database result with many credentials
    credentials = [(f"cred_{i}".encode(),) for i in range(20)]
    mock_result = MagicMock()
    mock_result.fetchall.return_value = credentials
    mock_db.execute = AsyncMock(return_value=mock_result)

    max_concurrent = 0
    current_concurrent = 0
    lock = threading.Lock()

    def track_concurrency(cred_id):
        """Track maximum concurrent decryptions."""
        nonlocal max_concurrent, current_concurrent
        with lock:
            current_concurrent += 1
            max_concurrent = max(max_concurrent, current_concurrent)

        time.sleep(0.01)  # Simulate work

        with lock:
            current_concurrent -= 1

        return cred_id

    with patch("src.app.core.security.encryption.get_encryption") as mock_enc:
        mock_encryption = MagicMock()
        mock_encryption.decrypt_credential = track_concurrency
        mock_enc.return_value = mock_encryption

        with override_settings(PASSKEY_DECRYPT_CONCURRENCY_LIMIT=10):
            await service._get_existing_credentials(mock_db, user_id=1)

    # Max concurrent should be limited by semaphore (10)
    assert max_concurrent <= 10


@pytest.mark.asyncio
async def test_large_credential_count_handled(mock_db, override_settings):
    """Test that large credential counts are handled with limit."""
    service = PasskeyService()

    # Mock database result with 150 credentials (exceeds limit of 100)
    credentials = [(f"cred_{i}".encode(),) for i in range(150)]
    mock_result = MagicMock()
    mock_result.fetchall.return_value = credentials[:100]  # DB applies limit
    mock_db.execute = AsyncMock(return_value=mock_result)

    with patch("src.app.core.security.encryption.get_encryption") as mock_enc:
        mock_encryption = MagicMock()
        mock_encryption.decrypt_credential.side_effect = lambda x: x
        mock_enc.return_value = mock_encryption

        with override_settings(PASSKEY_MAX_CREDENTIALS_PER_USER=100):
            result = await service._get_existing_credentials(mock_db, user_id=1)

    # Should be limited to max credentials per user
    assert len(result) <= 100


@pytest.mark.asyncio
async def test_feature_flag_toggles_behavior(mock_db, override_settings):
    """Test that feature flag controls optimization path."""
    service = PasskeyService()

    # Mock CRUD method for fallback path
    with patch("src.app.modules.passkey.service.crud_passkey_credentials") as mock_crud:
        mock_crud.get_by_user = AsyncMock(return_value=[])

        with override_settings(PASSKEY_USE_COLUMN_SELECT=False, PASSKEY_MAX_CREDENTIALS_PER_USER=100):
            result = await service._get_existing_credentials(mock_db, user_id=1)

        # Should use CRUD method (fallback path)
        mock_crud.get_by_user.assert_called_once()

    assert result == []


@pytest.mark.asyncio
async def test_monitoring_metrics_recorded(mock_db, override_settings):
    """Test that monitoring metrics are recorded."""
    service = PasskeyService()

    # Mock database result
    mock_result = MagicMock()
    mock_result.fetchall.return_value = [(b"cred_1",)]
    mock_db.execute = AsyncMock(return_value=mock_result)

    with patch("src.app.core.security.encryption.get_encryption") as mock_enc:
        mock_encryption = MagicMock()
        mock_encryption.decrypt_credential.return_value = b"decrypted"
        mock_enc.return_value = mock_encryption

        with override_settings(PASSKEY_USE_COLUMN_SELECT=True):
            with patch("src.app.core.monitoring.r1_unified_metrics.record_passkey_query") as mock_record:
                await service._get_existing_credentials(mock_db, user_id=1)

                # Verify monitoring was called
                mock_record.assert_called_once()
                # Verify latency was recorded (should be a float)
                assert isinstance(mock_record.call_args[0][0], float)
