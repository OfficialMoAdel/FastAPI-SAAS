"""Passkey tests - Contract and Unit tests combined.

This file contains:
- Contract tests: Endpoint existence verification
- Unit tests: DoS prevention via O(1) hash index
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# ============================================================================
# CONTRACT TESTS - Endpoint Existence
# ============================================================================


def test_passkey_api_endpoints_exist(client: TestClient):
    """Verify that passkey registration endpoints exist."""

    # Try to access start registration (should fail with 401 if unauth, not 404)
    response = client.post("/api/v1/auth/passkey/register")
    assert response.status_code != 404, "Endpoint /register not found"

    # Try to access verify registration
    response = client.post("/api/v1/auth/passkey/register/verify")
    assert response.status_code != 404, "Endpoint /register/verify not found"

    # Try to access list credentials
    response = client.get("/api/v1/auth/passkey/credentials")
    assert response.status_code != 404, "Endpoint /credentials not found"


# ============================================================================
# UNIT TESTS - DoS Prevention
# ============================================================================


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.execute = AsyncMock()
    return db


@pytest.fixture
def passkey_service():
    from src.app.modules.auth.services.passkey import PasskeyService

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
