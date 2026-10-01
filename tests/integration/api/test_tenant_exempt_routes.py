"""P1-6 counter-test: tenant-exempt routes still work through the app.

The guards now deny the NO_TENANT sentinel. The over-fix this must catch is
a 403 on a route that legitimately has no tenant — health probes and inbound
Stripe webhooks. Asserting on the exempt-path constants proves nothing; the
route has to be hit for real, with no X-Tenant-ID header.
"""

import pytest

EXEMPT_ROUTES = [
    "/health",
    "/health/live",
    "/health/ready",
    "/api/v1/health",
    "/api/v1/ready",
    "/docs",
    "/openapi.json",
]


@pytest.mark.parametrize("path", EXEMPT_ROUTES)
def test_exempt_route_does_not_deny_tenantless_caller(client_auth, path: str) -> None:
    """A tenantless caller must not be rejected on an exempt route.

    No X-Tenant-ID is sent, so the middleware binds the NO_TENANT sentinel.
    The guard changes must not turn that into a 403/401.
    """
    response = client_auth.get(path, headers={"Authorization": ""})

    assert response.status_code != 403, f"{path} denied a tenantless caller: {response.text[:200]}"
    assert response.status_code != 401, f"{path} required auth: {response.text[:200]}"


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/billing/webhook",
        "/api/v1/webhooks/stripe",
    ],
)
def test_webhook_routes_still_reach_their_handler(client_auth, path: str) -> None:
    """Inbound Stripe webhooks must not 403 on the missing tenant.

    A bad signature yields 400/401 from the handler — that proves the request
    reached the endpoint rather than being blocked by the tenant guard.
    """
    response = client_auth.post(path, json={})

    assert response.status_code != 403, f"{path} blocked the webhook: {response.text[:200]}"
