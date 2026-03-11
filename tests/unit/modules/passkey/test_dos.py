"""Unit tests for Passkey DoS prevention.

Ensures that credential lookup uses O(1) hash index and DOES NOT fall back
to O(n) decryption loop.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.modules.passkey.service import PasskeyService


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.execute = AsyncMock()
    return db


@pytest.fixture
def passkey_service():
    return PasskeyService()


@pytest.mark.asyncio
async def test_find_credential_by_id_no_fallback(passkey_service, mock_db):
    """Test that a hash miss returns None immediately without full scan."""
    # Setup
    raw_id = "test_cred_id"
    # Mock base64 decoding to work
    with patch("webauthn.helpers.base64url_to_bytes", return_value=b"bytes"):
        # Mock DB execute to return None (hash miss)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        # Execute
        result = await passkey_service._find_credential_by_id(mock_db, raw_id)

        # Assertions
        assert result is None

        # Verify only ONE DB call (the select by hash)
        assert mock_db.execute.call_count == 1

        # Verify the query filtered by hash
        call_args = mock_db.execute.call_args[0][0]
        assert "credential_id_hash" in str(call_args)
