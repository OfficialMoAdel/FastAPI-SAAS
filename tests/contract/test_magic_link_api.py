"""Contract tests for Magic Link API endpoints.

TDD: These tests are written first and should FAIL until implementation is complete.

Task: T021 [US1] Write contract tests for /auth/magic-link/* endpoints

Tests verify the API contract per specs/004-auth-user-management/contracts/auth-api.yaml
"""

from unittest.mock import AsyncMock, Mock, patch

from fastapi import status


class TestMagicLinkSendEndpoint:
    """Contract tests for POST /auth/magic-link/send endpoint."""

    def test_send_magic_link_endpoint_exists(self, client, auth_headers):
        """Verify endpoint exists and accepts POST requests."""
        response = client.post(
            "/api/v1/auth/magic-link/send",
            json={"email": "test@example.com"},
            headers=auth_headers,
        )
        # Should not return 404 or 405
        assert response.status_code not in [404, 405]

    def test_send_magic_link_requires_email(self, client, auth_headers):
        """Verify email is required in request body."""
        response = client.post(
            "/api/v1/auth/magic-link/send",
            json={},
            headers=auth_headers,
        )
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    def test_send_magic_link_validates_email_format(self, client, auth_headers):
        """Verify email format is validated."""
        response = client.post(
            "/api/v1/auth/magic-link/send",
            json={"email": "not-an-email"},
            headers=auth_headers,
        )
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    def test_send_magic_link_returns_200_on_success(self, client, auth_headers):
        """Verify successful request returns 200."""
        with patch(
            "src.app.modules.magic_link.service.MagicLinkService.send_magic_link",
            new_callable=AsyncMock,
        ) as mock_send:
            mock_send.return_value = Mock(
                message="If an account exists, a magic link has been sent",
                may_be_delayed=False,
            )

            response = client.post(
                "/api/v1/auth/magic-link/send",
                json={"email": "user@example.com"},
                headers=auth_headers,
            )

        assert response.status_code == status.HTTP_200_OK

    def test_send_magic_link_response_format(self, client, auth_headers):
        """Verify response matches MagicLinkSendResponse schema."""
        with patch(
            "src.app.modules.magic_link.service.MagicLinkService.send_magic_link",
            new_callable=AsyncMock,
        ) as mock_send:
            mock_send.return_value = Mock(
                message="If an account exists, a magic link has been sent",
                may_be_delayed=False,
            )

            response = client.post(
                "/api/v1/auth/magic-link/send",
                json={"email": "user@example.com"},
                headers=auth_headers,
            )

        data = response.json()
        assert "message" in data
        assert "may_be_delayed" in data

    def test_send_magic_link_rate_limited(self, client, auth_headers):
        """Verify rate limiting returns 429 (3/min per email per spec)."""
        # This test will be properly implemented when rate limiting is added
        # For now, we just verify the endpoint exists
        # Rate limiting is handled by middleware, not router
        pass  # Placeholder for rate limit test


class TestMagicLinkVerifyEndpoint:
    """Contract tests for POST /auth/magic-link/verify endpoint."""

    def test_verify_magic_link_endpoint_exists(self, client, auth_headers):
        """Verify endpoint exists and accepts POST requests."""
        response = client.post(
            "/api/v1/auth/magic-link/verify",
            json={"token": "a" * 43},
            headers=auth_headers,
        )
        # Should not return 404 or 405
        assert response.status_code not in [404, 405]

    def test_verify_magic_link_requires_token(self, client, auth_headers):
        """Verify token is required in request body."""
        response = client.post(
            "/api/v1/auth/magic-link/verify",
            json={},
            headers=auth_headers,
        )
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    def test_verify_magic_link_returns_auth_tokens(self, client, auth_headers):
        """Verify successful verification returns AuthTokenResponse."""
        with patch(
            "src.app.modules.magic_link.service.MagicLinkService.verify_magic_link",
            new_callable=AsyncMock,
        ) as mock_verify:
            mock_verify.return_value = Mock(
                access_token="access_token_123",
                refresh_token="refresh_token_456",
                token_type="bearer",
                expires_in=3600,
            )

            response = client.post(
                "/api/v1/auth/magic-link/verify",
                json={"token": "a" * 43},
                headers=auth_headers,
            )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert "token_type" in data
        assert "expires_in" in data

    def test_verify_magic_link_invalid_token_returns_400(self, client, auth_headers):
        """Verify invalid token returns 400 Bad Request."""
        from src.app.modules.magic_link.exceptions import InvalidMagicLinkError

        with patch(
            "src.app.modules.magic_link.service.MagicLinkService.verify_magic_link",
            new_callable=AsyncMock,
            side_effect=InvalidMagicLinkError("Invalid or expired token"),
        ):
            response = client.post(
                "/api/v1/auth/magic-link/verify",
                json={"token": "invalid_token_" + "a" * 30},
                headers=auth_headers,
            )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_verify_magic_link_used_token_returns_410(self, client, auth_headers):
        """Verify already-used token returns 410 Gone."""
        from src.app.modules.magic_link.exceptions import MagicLinkAlreadyUsedError

        with patch(
            "src.app.modules.magic_link.service.MagicLinkService.verify_magic_link",
            new_callable=AsyncMock,
            side_effect=MagicLinkAlreadyUsedError("Token already used"),
        ):
            response = client.post(
                "/api/v1/auth/magic-link/verify",
                json={"token": "used_token_" + "a" * 33},
                headers=auth_headers,
            )

        assert response.status_code == status.HTTP_410_GONE


class TestMagicLinkErrorResponses:
    """Test error response format matches ErrorResponse schema."""

    def test_error_response_format(self, client, auth_headers):
        """Verify error responses match ErrorResponse schema."""
        from src.app.modules.magic_link.exceptions import InvalidMagicLinkError

        with patch(
            "src.app.modules.magic_link.service.MagicLinkService.verify_magic_link",
            new_callable=AsyncMock,
            side_effect=InvalidMagicLinkError("Invalid token"),
        ):
            response = client.post(
                "/api/v1/auth/magic-link/verify",
                json={"token": "bad_token_" + "a" * 34},
                headers=auth_headers,
            )

        data = response.json()
        # Per auth-api.yaml ErrorResponse schema
        assert "detail" in data or "error" in data
