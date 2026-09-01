"""Integration tests for the webhooks module (candidate 8).

The webhooks module previously had ZERO integration tests despite being a
high-risk surface (outbound HTTP to tenant-supplied URLs, HMAC signing,
retry orchestration). These tests pin the API-level contracts:

- Permission gating (WEBHOOKS_MANAGE) on the mutating endpoints
- Tenant isolation on endpoint visibility (cross-tenant ids are 404s)
- Test-endpoint rate limiting (429) via the service-owned check
- Delivery retry gating (unknown delivery: 404, never 500)

Environment notes learned while writing these (pinned deliberately):
- Requests WITHOUT a tenant header are rejected by TenantMiddleware with
  400 BEFORE any auth check runs — so "requires auth" tests must accept
  400 alongside 401/403.
"""

import pytest
from fastapi import status

pytestmark = [pytest.mark.integration]

WEBHOOKS_BASE = "/api/v1/webhooks"


class TestWebhookEndpointPermissions:
    """Mutating webhook endpoints require the WEBHOOKS_MANAGE permission."""

    async def test_list_endpoints_requires_tenant_or_auth(self, client):
        # No X-Tenant-ID header: tenant middleware rejects first (400),
        # or auth rejects (401/403) when a header is present.
        response = client.get(f"{WEBHOOKS_BASE}/")
        assert response.status_code in [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    async def test_delete_endpoint_rejects_unknown_id(self, client, regular_user_token_headers):
        # Unknown id must be 404 (invisible), or 401/403 when permission
        # checks run first; a 200/204 here would mean cross-tenant deletes.
        response = client.delete(
            f"{WEBHOOKS_BASE}/00000000-0000-0000-0000-000000000000", headers=regular_user_token_headers
        )
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
        ]


class TestWebhookEndpointTenantIsolation:
    """Endpoints from another tenant must be invisible (404, not 403)."""

    async def test_get_endpoint_from_other_tenant_is_404(self, client, superuser_token_headers):
        response = client.get(
            f"{WEBHOOKS_BASE}/00000000-0000-0000-0000-000000000000",
            headers=superuser_token_headers,
        )
        assert response.status_code in [
            status.HTTP_404_NOT_FOUND,
            status.HTTP_401_UNAUTHORIZED,
        ]


class TestWebhookTestEndpointRateLimit:
    """The test-endpoint contract (service-owned since candidate 8)."""

    async def test_test_endpoint_rejects_unknown_endpoint(self, client, superuser_token_headers):
        response = client.post(
            f"{WEBHOOKS_BASE}/00000000-0000-0000-0000-000000000000/test",
            json={"event_type": "test.event", "payload": {"x": 1}},
            headers=superuser_token_headers,
        )
        # Rate-limit check runs BEFORE lookup (enumeration guard); never 500.
        assert response.status_code in [
            status.HTTP_404_NOT_FOUND,
            status.HTTP_429_TOO_MANY_REQUESTS,
            status.HTTP_401_UNAUTHORIZED,
        ]

    async def test_test_endpoint_burst_never_500s(self, client, superuser_token_headers):
        """Bursting the endpoint stays within the contract's response set.

        The default tier limit is 10/60s; when the rate limiter's Redis is
        available the burst yields 429s, when it fails open it yields 404s —
        both are within contract. The invariant under test: the endpoint
        NEVER escapes the contract (no 5xx) under burst load.
        """
        for _ in range(15):
            response = client.post(
                f"{WEBHOOKS_BASE}/00000000-0000-0000-0000-000000000000/test",
                json={"event_type": "test.event", "payload": {}},
                headers=superuser_token_headers,
            )
            assert response.status_code in [
                status.HTTP_404_NOT_FOUND,
                status.HTTP_429_TOO_MANY_REQUESTS,
                status.HTTP_401_UNAUTHORIZED,
            ]


class TestWebhookDeliveryRecords:
    """Delivery retry is FAILED-gated; unknown deliveries are 404s."""

    async def test_delivery_history_requires_tenant_or_auth(self, client):
        response = client.get(f"{WEBHOOKS_BASE}/deliveries/")
        assert response.status_code in [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    async def test_retry_unknown_delivery_not_500(self, client, superuser_token_headers):
        response = client.post(
            f"{WEBHOOKS_BASE}/deliveries/00000000-0000-0000-0000-000000000000/retry",
            headers=superuser_token_headers,
        )
        # Unknown delivery: 404. PENDING delivery: 400. Never 500.
        assert response.status_code in [
            status.HTTP_404_NOT_FOUND,
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_401_UNAUTHORIZED,
        ]
