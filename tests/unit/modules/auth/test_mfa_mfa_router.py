"""
MFA Router Tests

Unit tests for the MFA router endpoints, focusing on rate limiting and lockout mechanism.
"""

from collections.abc import Generator
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from src.app.shared.exceptions import RateLimitExceededError as RateLimitException


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
            "src.app.modules.auth.routers.mfa.check_mfa_rate_limit",
            new_callable=AsyncMock,
            side_effect=mock_check_mfa_rate_limit,
        ):
            from src.app.shared.exceptions import UnauthorizedError as UnauthorizedException

            with patch(
                "src.app.modules.auth.services.mfa.MFAService.verify_totp_token",
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
            "src.app.modules.auth.routers.mfa.check_mfa_rate_limit",
            new_callable=AsyncMock,
            side_effect=mock_check_mfa_rate_limit,
        ):
            from src.app.shared.exceptions import UnauthorizedError as UnauthorizedException

            with patch(
                "src.app.modules.auth.services.mfa.MFAService.verify_totp_token",
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
        from types import SimpleNamespace

        attempt_count = 0

        async def mock_check_security_budget(budget, identifier):
            nonlocal attempt_count
            attempt_count += 1
            assert (budget, identifier) == ("mfa_verify", "a" * 32)
            return SimpleNamespace(is_limited=attempt_count > 5, period=60, retry_after=23)

        headers = {"X-Tenant-ID": "test-tenant"}

        # mfa_token must be 32-64 characters per schema validation
        valid_mfa_token = "a" * 32  # 32 char token

        with patch(
            "src.app.modules.auth.services.mfa.check_security_budget",
            new_callable=AsyncMock,
            side_effect=mock_check_security_budget,
        ) as budget:
            with patch("src.app.modules.auth.services.mfa.get_redis", return_value=None) as redis:
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
                assert budget.await_count == 6
                assert redis.call_count == 5  # The sixth attempt is rejected before Redis.

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
            "src.app.modules.auth.routers.mfa.check_mfa_rate_limit",
            new_callable=AsyncMock,
            side_effect=mock_check_mfa_rate_limit,
        ):
            with patch(
                "src.app.modules.auth.services.mfa.MFAService.verify_totp_token",
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
        """Completion charges the service verification budget to the pending token."""
        from types import SimpleNamespace

        token = "a" * 32
        with (
            patch(
                "src.app.modules.auth.services.mfa.check_security_budget",
                new_callable=AsyncMock,
                return_value=SimpleNamespace(is_limited=False),
            ) as budget,
            patch("src.app.modules.auth.services.mfa.get_redis", return_value=None),
        ):
            response = client_with_rate_limit.post(
                "/api/v1/mfa/complete-login",
                json={"mfa_token": token, "totp_token": "123456"},
                headers={"X-Tenant-ID": "test-tenant"},
            )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        budget.assert_awaited_once_with("mfa_verify", token)


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


class TestMFAMutateRateLimiting:
    """P1-5: /disable and /backup-codes must enforce a per-user mutation budget."""

    def test_mutate_limiter_uses_separate_budget(self):
        """Mutate budget must cross a distinct engine budget from verify."""
        import asyncio

        from src.app.modules.auth.routers import mfa as mfa_router

        seen: dict = {}

        async def fake_check(request, identifier=None, budget="mfa_verify"):
            seen.update({"identifier": identifier, "budget": budget})

        with patch.object(mfa_router, "check_mfa_rate_limit", side_effect=fake_check):
            asyncio.get_event_loop().run_until_complete(mfa_router.check_mfa_mutate_rate_limit(Mock(), 42))

        assert seen == {"identifier": "42", "budget": "mfa_mutate"}

    def test_disable_enforces_mutate_limit(self, client_with_rate_limit):
        """Should return 429 when the mutate limiter trips on /disable."""
        headers = {
            "Authorization": "Bearer test-token",
            "X-Tenant-ID": "test-tenant",
        }

        with patch(
            "src.app.modules.auth.routers.mfa.check_mfa_mutate_rate_limit",
            new_callable=AsyncMock,
            side_effect=RateLimitException("Too many MFA mutation attempts."),
        ):
            response = client_with_rate_limit.post(
                "/api/v1/mfa/disable",
                json={"password": "test_password", "totp_token": "123456"},
                headers=headers,
            )
            assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS

    def test_disable_calls_mutate_limiter_then_service(self, client_with_rate_limit):
        """Limiter runs before the service; service still reached when allowed."""
        headers = {
            "Authorization": "Bearer test-token",
            "X-Tenant-ID": "test-tenant",
        }

        with (
            patch(
                "src.app.modules.auth.routers.mfa.check_mfa_mutate_rate_limit",
                new_callable=AsyncMock,
            ) as mock_limit,
            patch(
                "src.app.modules.auth.services.mfa.MFAService.disable_mfa",
                new_callable=AsyncMock,
                return_value={"message": "MFA disabled successfully"},
            ),
        ):
            response = client_with_rate_limit.post(
                "/api/v1/mfa/disable",
                json={"password": "test_password", "totp_token": "123456"},
                headers=headers,
            )
            assert response.status_code == status.HTTP_200_OK
            mock_limit.assert_awaited_once()

    def test_backup_codes_enforces_mutate_limit(self, client_with_rate_limit):
        """Should return 429 when the mutate limiter trips on /backup-codes."""
        headers = {
            "Authorization": "Bearer test-token",
            "X-Tenant-ID": "test-tenant",
        }

        with patch(
            "src.app.modules.auth.routers.mfa.check_mfa_mutate_rate_limit",
            new_callable=AsyncMock,
            side_effect=RateLimitException("Too many MFA mutation attempts."),
        ):
            response = client_with_rate_limit.post(
                "/api/v1/mfa/backup-codes",
                json={"password": "test_password", "totp_token": "123456"},
                headers=headers,
            )
            assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS

    def test_backup_codes_calls_mutate_limiter_then_service(self, client_with_rate_limit):
        """Limiter runs before the service; service still reached when allowed."""
        headers = {
            "Authorization": "Bearer test-token",
            "X-Tenant-ID": "test-tenant",
        }

        with (
            patch(
                "src.app.modules.auth.routers.mfa.check_mfa_mutate_rate_limit",
                new_callable=AsyncMock,
            ) as mock_limit,
            patch(
                "src.app.modules.auth.services.mfa.MFAService.regenerate_backup_codes",
                new_callable=AsyncMock,
                return_value={"message": "New backup codes generated", "backup_codes": []},
            ),
        ):
            response = client_with_rate_limit.post(
                "/api/v1/mfa/backup-codes",
                json={"password": "test_password", "totp_token": "123456"},
                headers=headers,
            )
            assert response.status_code == status.HTTP_200_OK
            mock_limit.assert_awaited_once()
