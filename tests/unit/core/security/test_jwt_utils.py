"""Unit tests for JWT claim extraction utilities.

Tests the jwt_utils module which provides request-scoped JWT caching
to eliminate redundant decoding in the request lifecycle.
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock, patch

import jwt
import pytest

from src.app.core.security.jwt_utils import (
    JWT_AUDIENCE,
    decode_jwt_token,
    extract_jwt_claims_to_state,
    extract_token_from_header,
    get_cached_jwt_payload,
    has_cached_jwt_payload,
)

# =============================================================================
# FIXTURES
# =============================================================================


@pytest.fixture
def mock_request() -> MagicMock:
    """Create a mock FastAPI request with state."""
    request = MagicMock()
    request.state = MagicMock(spec=[])  # Empty spec = no attributes
    request.headers = {}
    return request


@pytest.fixture
def valid_token_payload() -> dict[str, Any]:
    """Create a valid JWT payload."""
    now = int(time.time())
    return {
        "sub": "user-123",
        "tenant_id": "tenant-456",
        "exp": now + 3600,
        "iat": now,
        "aud": JWT_AUDIENCE,
        "type": "access",
    }


@pytest.fixture
def test_secret() -> str:
    """Test secret key for HS256."""
    return "test-secret-key-for-testing-only"


@pytest.fixture
def valid_token(valid_token_payload: dict[str, Any], test_secret: str) -> str:
    """Create a valid JWT token."""
    return jwt.encode(valid_token_payload, test_secret, algorithm="HS256")


@pytest.fixture
def expired_token(test_secret: str) -> str:
    """Create an expired JWT token."""
    payload = {
        "sub": "user-123",
        "exp": int(time.time()) - 3600,  # Expired 1 hour ago
        "iat": int(time.time()) - 7200,
        "aud": JWT_AUDIENCE,
    }
    return jwt.encode(payload, test_secret, algorithm="HS256")


# =============================================================================
# extract_token_from_header TESTS
# =============================================================================


class TestExtractTokenFromHeader:
    """Tests for extract_token_from_header function."""

    def test_valid_bearer_token(self) -> None:
        """Should extract token from valid Bearer header."""
        token = extract_token_from_header("Bearer abc123xyz")
        assert token == "abc123xyz"

    def test_bearer_case_insensitive(self) -> None:
        """Should handle lowercase 'bearer'."""
        token = extract_token_from_header("bearer abc123xyz")
        assert token == "abc123xyz"

    def test_none_header(self) -> None:
        """Should return None for missing header."""
        token = extract_token_from_header(None)
        assert token is None

    def test_empty_header(self) -> None:
        """Should return None for empty header."""
        token = extract_token_from_header("")
        assert token is None

    def test_missing_bearer_prefix(self) -> None:
        """Should return None without Bearer prefix."""
        token = extract_token_from_header("abc123xyz")
        assert token is None

    def test_wrong_auth_scheme(self) -> None:
        """Should return None for non-Bearer schemes."""
        token = extract_token_from_header("Basic abc123xyz")
        assert token is None

    def test_too_many_parts(self) -> None:
        """Should return None if too many space-separated parts."""
        token = extract_token_from_header("Bearer abc123 extra")
        assert token is None


# =============================================================================
# decode_jwt_token TESTS
# =============================================================================


class TestDecodeJwtToken:
    """Tests for decode_jwt_token function."""

    def test_valid_token_decodes(self, valid_token: str, test_secret: str) -> None:
        """Should decode a valid token successfully."""
        with patch("src.app.core.security.jwt_utils._get_parsed_verification_key", return_value=test_secret):
            with patch("src.app.core.security.jwt_utils._get_algorithm", return_value="HS256"):
                payload = decode_jwt_token(valid_token)

        assert payload is not None
        assert payload["sub"] == "user-123"
        assert payload["tenant_id"] == "tenant-456"

    def test_expired_token_returns_none(self, expired_token: str, test_secret: str) -> None:
        """Should return None for expired token."""
        with patch("src.app.core.security.jwt_utils._get_parsed_verification_key", return_value=test_secret):
            with patch("src.app.core.security.jwt_utils._get_algorithm", return_value="HS256"):
                payload = decode_jwt_token(expired_token)

        assert payload is None

    def test_invalid_signature_returns_none(self, valid_token: str) -> None:
        """Should return None for wrong secret."""
        with patch("src.app.core.security.jwt_utils._get_parsed_verification_key", return_value="wrong-secret"):
            with patch("src.app.core.security.jwt_utils._get_algorithm", return_value="HS256"):
                payload = decode_jwt_token(valid_token)

        assert payload is None

    def test_malformed_token_returns_none(self, test_secret: str) -> None:
        """Should return None for malformed token."""
        with patch("src.app.core.security.jwt_utils._get_parsed_verification_key", return_value=test_secret):
            with patch("src.app.core.security.jwt_utils._get_algorithm", return_value="HS256"):
                payload = decode_jwt_token("not.a.valid.token")

        assert payload is None

    def test_empty_token_returns_none(self, test_secret: str) -> None:
        """Should return None for empty token."""
        with patch("src.app.core.security.jwt_utils._get_parsed_verification_key", return_value=test_secret):
            with patch("src.app.core.security.jwt_utils._get_algorithm", return_value="HS256"):
                payload = decode_jwt_token("")

        assert payload is None


# =============================================================================
# extract_jwt_claims_to_state TESTS
# =============================================================================


class TestExtractJwtClaimsToState:
    """Tests for extract_jwt_claims_to_state function."""

    @pytest.mark.asyncio
    async def test_caches_payload_in_request_state(
        self, mock_request: MagicMock, valid_token: str, test_secret: str
    ) -> None:
        """Should cache decoded payload in request.state."""
        mock_request.headers = {"Authorization": f"Bearer {valid_token}"}

        with patch("src.app.core.security.jwt_utils._get_parsed_verification_key", return_value=test_secret):
            with patch("src.app.core.security.jwt_utils._get_algorithm", return_value="HS256"):
                payload = await extract_jwt_claims_to_state(mock_request)

        assert payload is not None
        assert payload["sub"] == "user-123"
        assert mock_request.state.jwt_payload == payload

    @pytest.mark.asyncio
    async def test_returns_cached_payload_on_second_call(
        self, mock_request: MagicMock, valid_token_payload: dict[str, Any]
    ) -> None:
        """Should return cached payload without re-decoding."""
        # Pre-cache the payload
        mock_request.state.jwt_payload = valid_token_payload

        payload = await extract_jwt_claims_to_state(mock_request)

        assert payload == valid_token_payload
        # Headers not accessed since cache hit
        assert "Authorization" not in mock_request.headers

    @pytest.mark.asyncio
    async def test_no_auth_header_returns_none(self, mock_request: MagicMock) -> None:
        """Should return None when no Authorization header."""
        mock_request.headers = {}

        payload = await extract_jwt_claims_to_state(mock_request)

        assert payload is None

    @pytest.mark.asyncio
    async def test_invalid_token_returns_none(self, mock_request: MagicMock, test_secret: str) -> None:
        """Should return None for invalid token."""
        mock_request.headers = {"Authorization": "Bearer invalid.token.here"}

        with patch("src.app.core.security.jwt_utils._get_parsed_verification_key", return_value=test_secret):
            with patch("src.app.core.security.jwt_utils._get_algorithm", return_value="HS256"):
                payload = await extract_jwt_claims_to_state(mock_request)

        assert payload is None


# =============================================================================
# get_cached_jwt_payload TESTS
# =============================================================================


class TestGetCachedJwtPayload:
    """Tests for get_cached_jwt_payload function."""

    def test_returns_cached_payload(self, mock_request: MagicMock, valid_token_payload: dict[str, Any]) -> None:
        """Should return cached payload when present."""
        mock_request.state.jwt_payload = valid_token_payload

        payload = get_cached_jwt_payload(mock_request)

        assert payload == valid_token_payload

    def test_returns_none_when_not_cached(self, mock_request: MagicMock) -> None:
        """Should return None when not cached."""
        payload = get_cached_jwt_payload(mock_request)

        assert payload is None


# =============================================================================
# has_cached_jwt_payload TESTS
# =============================================================================


class TestHasCachedJwtPayload:
    """Tests for has_cached_jwt_payload function."""

    def test_returns_true_when_cached(self, mock_request: MagicMock, valid_token_payload: dict[str, Any]) -> None:
        """Should return True when payload is cached."""
        mock_request.state.jwt_payload = valid_token_payload

        assert has_cached_jwt_payload(mock_request) is True

    def test_returns_false_when_not_cached(self, mock_request: MagicMock) -> None:
        """Should return False when not cached."""
        assert has_cached_jwt_payload(mock_request) is False

    def test_returns_false_when_cached_none(self, mock_request: MagicMock) -> None:
        """Should return False when cached value is None."""
        mock_request.state.jwt_payload = None

        assert has_cached_jwt_payload(mock_request) is False


# =============================================================================
# PERFORMANCE TESTS
# =============================================================================


class TestJwtUtilsPerformance:
    """Performance-related tests for jwt_utils."""

    @pytest.mark.asyncio
    async def test_cache_prevents_double_decode(
        self, mock_request: MagicMock, valid_token: str, test_secret: str
    ) -> None:
        """Verify caching prevents redundant JWT decoding."""
        mock_request.headers = {"Authorization": f"Bearer {valid_token}"}
        decode_count = 0

        original_decode = jwt.decode

        def counting_decode(*args: Any, **kwargs: Any) -> dict[str, Any]:
            nonlocal decode_count
            decode_count += 1
            return original_decode(*args, **kwargs)

        with patch("src.app.core.security.jwt_utils._get_parsed_verification_key", return_value=test_secret):
            with patch("src.app.core.security.jwt_utils._get_algorithm", return_value="HS256"):
                with patch("src.app.core.security.jwt_utils.jwt.decode", side_effect=counting_decode):
                    # First call - should decode
                    await extract_jwt_claims_to_state(mock_request)
                    assert decode_count == 1

                    # Second call - should use cache
                    await extract_jwt_claims_to_state(mock_request)
                    assert decode_count == 1  # Still 1, no additional decode

                    # Third call - still cached
                    await extract_jwt_claims_to_state(mock_request)
                    assert decode_count == 1
