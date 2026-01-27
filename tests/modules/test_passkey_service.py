import pytest
from unittest.mock import Mock, AsyncMock, patch
from src.app.modules.passkey.service import PasskeyService, PublicKeyCredentialDescriptor


@pytest.fixture
def mock_db_session():
    return AsyncMock()


@pytest.mark.asyncio
async def test_decrypt_credentials_parallel():
    """Verify parallel decryption helper."""
    service = PasskeyService()

    # Mock encryption
    with patch("src.app.core.security.encryption.get_encryption") as mock_get_enc:
        mock_enc = Mock()
        mock_get_enc.return_value = mock_enc
        # Decrypt returns bytes
        mock_enc.decrypt_credential.return_value = b"decrypted_id"

        # Test data: list of (id, transports)
        data = [
            ("enc1", None),
            ("enc2", "usb"),
        ]

        results = await service._decrypt_credentials_parallel(data, "user1")

        assert len(results) == 2
        assert isinstance(results[0], PublicKeyCredentialDescriptor)
        assert results[0].id == b"decrypted_id"
        assert results[0].transports is None

        assert results[1].id == b"decrypted_id"
        assert results[1].transports == "usb"

        assert mock_enc.decrypt_credential.call_count == 2


@pytest.mark.asyncio
async def test_get_existing_credentials_optimized(mock_db_session):
    """Test _get_existing_credentials with optimized column select."""
    service = PasskeyService()

    # Mock execute result
    mock_result = Mock()
    # Return list of tuples (credential_id,)
    mock_result.fetchall.return_value = [("enc1",), ("enc2",)]
    mock_db_session.execute.return_value = mock_result

    # Mock settings to enable optimization
    with (
        patch("src.app.core.config.settings.r1_unified.PASSKEY_USE_COLUMN_SELECT", True),
        patch.object(service, "_decrypt_credentials_parallel", new_callable=AsyncMock) as mock_helper,
    ):
        await service._get_existing_credentials(mock_db_session, 1)

        # Verify call to helper
        mock_helper.assert_awaited_once()
        args, _ = mock_helper.call_args
        creds_list, user_id = args

        assert len(creds_list) == 2
        assert creds_list[0] == ("enc1", None)
        assert creds_list[1] == ("enc2", None)
        assert user_id == 1


@pytest.mark.asyncio
async def test_get_existing_credentials_fallback(mock_db_session):
    """Test _get_existing_credentials with fallback CRUD."""
    service = PasskeyService()

    # Mock CRUD return
    mock_cred = Mock()
    mock_cred.credential_id = "enc1"
    mock_cred.transports = "usb"

    with (
        patch("src.app.core.config.settings.r1_unified.PASSKEY_USE_COLUMN_SELECT", False),
        patch("src.app.modules.passkey.crud.crud_passkey_credentials.get_by_user", new_callable=AsyncMock) as mock_get,
        patch.object(service, "_decrypt_credentials_parallel", new_callable=AsyncMock) as mock_helper,
    ):
        mock_get.return_value = [mock_cred]

        await service._get_existing_credentials(mock_db_session, 1)

        mock_helper.assert_awaited_once()
        args, _ = mock_helper.call_args
        creds_list, _ = args

        assert creds_list[0] == ("enc1", "usb")
