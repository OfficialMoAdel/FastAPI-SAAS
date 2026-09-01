"""
MFA Router Tests

Unit tests for the MFA router endpoints, focusing on rate limiting and lockout mechanism.
"""

from collections.abc import Generator
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from src.app.core.exceptions.http_exceptions import RateLimitException


@pytest.fixture
def client_with_rate_limit(get_test_app) -> Generator[TestClient, None, None]:
    """Client fixture with rate limiting enabled for MFA tests.

    Uses the ctx seam: overrides ``get_request_context`` with a real
    ``RequestContext`` (db is unused by these tests — service calls are
    class-level patched; the pre-conversion fixture likewise injected
    ``async_get_db -> None``).
    """
    from src.app.api.deps import RequestContext, get_request_context

    app = get_test_app

    fake_tenant = Mock()
    fake_tenant.id = 1
    fake_tenant.slug = "test-tenant"
    fake_tenant.is_active = True

    fake_superuser = Mock(
        id=1,
        username="testuser",
        email="test@example.com",
        is_superuser=True,
        is_active=True,
        tenant_id=1,
    )
    fake_superuser.__getitem__ = lambda self, key: getattr(self, key)

    fake_ctx = RequestContext(user=fake_superuser, tenant=fake_tenant, db=Mock())
    app.dependency_overrides[get_request_context] = lambda: fake_ctx

    with (
        patch("src.app.core.setup.create_tables", new_callable=AsyncMock),
        patch("src.app.core.cache.warmer.warm_caches", new_callable=AsyncMock),
        # DON'T mock rate limiter - let tests control it
        patch(
            "src.app.middleware.tenant.TenantMiddleware._get_tenant_by_slug",
            new_callable=AsyncMock,
            return_value=fake_tenant,
        ),
    ):
        with TestClient(app) as _client:
            yield _client

    app.dependency_overrides.clear()


class TestMFARateLimiting:
    """Test MFA rate limiting and lockout mechanism."""

    def test_verify_endpoint_rate_limits_after_5_attempts(self, client_with_rate_limit):
        """Should rate limit after 5 failed verify attempts."""
        attempt_count = 0

        async def mock_check_mfa_rate_limit(request, identifier=None):
            """Mock MFA rate limiter that tracks attempts and raises after limit."""
            nonlocal attempt_count
            attempt_count += 1

            if attempt_count > 5:
                raise RateLimitException("Too many MFA verification attempts. Please try again in 60 seconds.")

        headers = {
            "Authorization": "Bearer test-token",
            "X-Tenant-ID": "test-tenant",
        }

        with patch(
            "src.app.modules.mfa.router.check_mfa_rate_limit",
            new_callable=AsyncMock,
            side_effect=mock_check_mfa_rate_limit,
        ):
            from src.app.core.exceptions.http_exceptions import UnauthorizedException

            with patch(
                "src.app.modules.mfa.service.MFAService.verify_totp_token",
                new_callable=AsyncMock,
                side_effect=UnauthorizedException("Invalid MFA token"),
            ):
                # First 5 attempts should fail with 401 (invalid token)
                for i in range(5):
                    response = client_with_rate_limit.post(
                        "/api/v1/mfa/verify",
                        json={"totp_token": "000000", "backup_code": None},
                        headers=headers,
                    )
                    assert response.status_code == status.HTTP_401_UNAUTHORIZED

                # 6th attempt should be rate limited with 429
                response = client_with_rate_limit.post(
                    "/api/v1/mfa/verify",
                    json={"totp_token": "000000", "backup_code": None},
                    headers=headers,
                )
                assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
                assert "Too many MFA verification attempts" in response.json()["detail"]

    def test_verify_endpoint_respects_rate_limit_period(self, client_with_rate_limit):
        """Should allow requests after rate limit period expires."""
        attempt_count = 0
        is_limited = False

        async def mock_check_mfa_rate_limit(request, identifier=None):
            """Mock MFA rate limiter with simulated period expiry."""
            nonlocal attempt_count, is_limited
            attempt_count += 1

            if is_limited or attempt_count > 5:
                is_limited = True
                raise RateLimitException("Too many MFA verification attempts. Please try again in 60 seconds.")

        headers = {
            "Authorization": "Bearer test-token",
            "X-Tenant-ID": "test-tenant",
        }

        with patch(
            "src.app.modules.mfa.router.check_mfa_rate_limit",
            new_callable=AsyncMock,
            side_effect=mock_check_mfa_rate_limit,
        ):
            from src.app.core.exceptions.http_exceptions import UnauthorizedException

            with patch(
                "src.app.modules.mfa.service.MFAService.verify_totp_token",
                new_callable=AsyncMock,
                side_effect=UnauthorizedException("Invalid MFA token"),
            ):
                # Make 5 failed attempts to hit rate limit
                for i in range(5):
                    response = client_with_rate_limit.post(
                        "/api/v1/mfa/verify",
                        json={"totp_token": "000000", "backup_code": None},
                        headers=headers,
                    )
                    assert response.status_code == status.HTTP_401_UNAUTHORIZED

                # 6th attempt should be rate limited
                response = client_with_rate_limit.post(
                    "/api/v1/mfa/verify",
                    json={"totp_token": "000000", "backup_code": None},
                    headers=headers,
                )
                assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS

                # Reset the counter (simulating period expiry)
                attempt_count = 0
                is_limited = False

                # After reset, should allow requests again
                response = client_with_rate_limit.post(
                    "/api/v1/mfa/verify",
                    json={"totp_token": "000000", "backup_code": None},
                    headers=headers,
                )
                assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_complete_login_endpoint_rate_limits_after_5_attempts(self, client_with_rate_limit):
        """Should rate limit after 5 failed complete login attempts."""
        attempt_count = 0

        async def mock_check_mfa_rate_limit(request, identifier=None):
            """Mock MFA rate limiter that tracks attempts."""
            nonlocal attempt_count
            attempt_count += 1

            if attempt_count > 5:
                raise RateLimitException("Too many MFA verification attempts. Please try again in 60 seconds.")

        headers = {"X-Tenant-ID": "test-tenant"}

        # mfa_token must be 32-64 characters per schema validation
        valid_mfa_token = "a" * 32  # 32 char token

        with patch(
            "src.app.modules.mfa.router.check_mfa_rate_limit",
            new_callable=AsyncMock,
            side_effect=mock_check_mfa_rate_limit,
        ):
            with patch(
                "src.app.core.security.pending_token_store.PendingTokenStore.consume",
                new_callable=AsyncMock,
                return_value=None,
            ):
                # Make 5 failed attempts with invalid MFA pending token
                for i in range(5):
                    response = client_with_rate_limit.post(
                        "/api/v1/mfa/complete-login",
                        json={
                            "mfa_token": valid_mfa_token,
                            "totp_token": "000000",
                            "backup_code": None,
                        },
                        headers=headers,
                    )
                    assert response.status_code == status.HTTP_401_UNAUTHORIZED

                # 6th attempt should be rate limited
                response = client_with_rate_limit.post(
                    "/api/v1/mfa/complete-login",
                    json={
                        "mfa_token": valid_mfa_token,
                        "totp_token": "000000",
                        "backup_code": None,
                    },
                    headers=headers,
                )
                assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
                assert "Too many MFA verification attempts" in response.json()["detail"]

    def test_rate_limit_uses_user_id_as_identifier(self, client_with_rate_limit):
        """Rate limiting should be per-user, not per-request."""
        captured_identifiers = []

        async def mock_check_mfa_rate_limit(request, identifier=None):
            """Mock that captures the rate limit identifier."""
            captured_identifiers.append(identifier)

        headers = {
            "Authorization": "Bearer test-token",
            "X-Tenant-ID": "test-tenant",
        }

        with patch(
            "src.app.modules.mfa.router.check_mfa_rate_limit",
            new_callable=AsyncMock,
            side_effect=mock_check_mfa_rate_limit,
        ):
            with patch(
                "src.app.modules.mfa.service.MFAService.verify_totp_token",
                new_callable=AsyncMock,
                return_value={"valid": True, "message": "Valid token"},
            ):
                # Make multiple requests - should use same identifier (user_id)
                for i in range(3):
                    client_with_rate_limit.post(
                        "/api/v1/mfa/verify",
                        json={"totp_token": "123456", "backup_code": None},
                        headers=headers,
                    )

                # All identifiers should be the same (user-based)
                assert len(captured_identifiers) == 3
                assert len(set(captured_identifiers)) == 1  # All same identifier
                assert captured_identifiers[0] == "1"  # user_id from mock

    def test_rate_limit_uses_token_as_identifier(self, client_with_rate_limit):
        """Rate limiting should use mfa_token as identifier for complete-login."""
        captured_identifiers = []

        async def mock_check_mfa_rate_limit(request, identifier=None):
            """Mock that captures the rate limit identifier."""
            captured_identifiers.append(identifier)

        headers = {"X-Tenant-ID": "test-tenant"}

        # mfa_token must be 32-64 characters per schema validation
        valid_mfa_token = "test_pending_token_12345678_abcd"  # 32+ char token

        with patch(
            "src.app.modules.mfa.router.check_mfa_rate_limit",
            new_callable=AsyncMock,
            side_effect=mock_check_mfa_rate_limit,
        ):
            with patch(
                "src.app.core.security.pending_token_store.PendingTokenStore.consume",
                new_callable=AsyncMock,
                return_value=None,
            ):
                # For complete-login endpoint, uses mfa_token as identifier
                client_with_rate_limit.post(
                    "/api/v1/mfa/complete-login",
                    json={
                        "mfa_token": valid_mfa_token,
                        "totp_token": "123456",
                        "backup_code": None,
                    },
                    headers=headers,
                )

                # Identifier should be the mfa_token
                assert len(captured_identifiers) == 1
                assert captured_identifiers[0] == valid_mfa_token


class TestMFAEndpointAuthentication:
    """Test MFA endpoint authentication requirements.

    These tests verify that MFA endpoints require authentication.
    The tenant middleware runs before auth, so we must provide a valid tenant header.
    """

    def test_setup_requires_authentication(self, unauthenticated_client):
        """Should return 401 when not authenticated."""
        headers = {"X-Tenant-ID": "test-tenant-integration"}
        response = unauthenticated_client.post("/api/v1/mfa/setup", headers=headers)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_enable_requires_authentication(self, unauthenticated_client):
        """Should return 401 when not authenticated."""
        headers = {"X-Tenant-ID": "test-tenant-integration"}
        response = unauthenticated_client.post(
            "/api/v1/mfa/enable",
            json={"secret": "test_secret", "totp_token": "123456"},
            headers=headers,
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_verify_requires_authentication(self, unauthenticated_client):
        """Should return 401 when not authenticated."""
        headers = {"X-Tenant-ID": "test-tenant-integration"}
        response = unauthenticated_client.post(
            "/api/v1/mfa/verify",
            json={"totp_token": "123456", "backup_code": None},
            headers=headers,
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_disable_requires_authentication(self, unauthenticated_client):
        """Should return 401 when not authenticated."""
        headers = {"X-Tenant-ID": "test-tenant-integration"}
        response = unauthenticated_client.post(
            "/api/v1/mfa/disable",
            json={"password": "test_password", "totp_token": "123456"},
            headers=headers,
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_status_requires_authentication(self, unauthenticated_client):
        """Should return 401 when not authenticated."""
        headers = {"X-Tenant-ID": "test-tenant-integration"}
        response = unauthenticated_client.get("/api/v1/mfa/status", headers=headers)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_backup_codes_requires_authentication(self, unauthenticated_client):
        """Should return 401 when not authenticated."""
        headers = {"X-Tenant-ID": "test-tenant-integration"}
        response = unauthenticated_client.post(
            "/api/v1/mfa/backup-codes",
            json={"password": "test_password", "totp_token": "123456"},
            headers=headers,
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestMFAEndpointRegistration:
    """Test MFA router endpoint registration."""

    def test_mfa_router_registered(self, client):
        """MFA router should be registered in OpenAPI spec."""
        response = client.get("/openapi.json")
        assert response.status_code == 200

        spec = response.json()
        paths = spec["paths"]

        # Check that MFA endpoints exist
        assert "/api/v1/mfa/setup" in paths
        assert "/api/v1/mfa/enable" in paths
        assert "/api/v1/mfa/verify" in paths
        assert "/api/v1/mfa/disable" in paths
        assert "/api/v1/mfa/status" in paths
        assert "/api/v1/mfa/backup-codes" in paths
        assert "/api/v1/mfa/complete-login" in paths

    def test_mfa_endpoint_tags(self, client):
        """MFA endpoints should have correct tags."""
        response = client.get("/openapi.json")
        spec = response.json()

        # Check tags
        setup_endpoint = spec["paths"]["/api/v1/mfa/setup"]["post"]
        assert "mfa" in setup_endpoint["tags"]

        verify_endpoint = spec["paths"]["/api/v1/mfa/verify"]["post"]
        assert "mfa" in verify_endpoint["tags"]
