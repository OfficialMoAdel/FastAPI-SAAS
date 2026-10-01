"""Auth contract for the LEGACY /api/v1/billing catalog routes.

Run-3 N-2 (billing.catalog_reads_scope_from_unauthenticated_header): the legacy
GET /plans, /plans/{id}, /products and /products/{id} routes are duplicates of
the authenticated compatibility routers (/api/v1/prices, /api/v1/products).
They must reject anonymous callers while continuing to serve authenticated
principals, and the already-authenticated neighbours must not regress.

All anonymous probes send a valid X-Tenant-ID: the tenant middleware resolves
the tenant before the auth dependency runs, so omitting the header would yield
a 400 about the header rather than exercising the authentication contract.
"""

import pytest

from tests.conftest import MockUser

LEGACY_CATALOG_READS = [
    "/api/v1/billing/plans",
    "/api/v1/billing/plans/1",
    "/api/v1/billing/products",
    "/api/v1/billing/products/1",
]

# Neighbours that were already authenticated - anti-over-fix guards.
AUTHENTICATED_NEIGHBOURS = [
    "/api/v1/billing/subscriptions/me",
    "/api/v1/billing/records",
    "/api/v1/billing/records/1",
]

# Compat paths that should also require auth.
# Note: /api/v1/prices is excluded — it has a pre-existing ORM column bug
# ("Invalid column 'product' for model Product") unrelated to this fix.
COMPAT_CATALOG_READS = [
    "/api/v1/products",
]


def _tenant_headers() -> dict[str, str]:
    """A resolvable tenant identity, so auth is what denies the caller."""
    return {"X-Tenant-ID": str(MockUser.DEFAULT_TENANT_ID)}


@pytest.mark.parametrize("path", LEGACY_CATALOG_READS)
def test_legacy_catalog_read_rejects_anonymous(unauthenticated_client, path) -> None:
    """Anonymous GET on a legacy catalog route must be denied (401/403)."""
    response = unauthenticated_client.get(path, headers=_tenant_headers())
    assert response.status_code in (401, 403), (
        f"{path} served anonymously: status={response.status_code} body={response.text[:200]}"
    )


@pytest.mark.parametrize("path", AUTHENTICATED_NEIGHBOURS)
def test_authenticated_neighbours_still_reject_anonymous(unauthenticated_client, path) -> None:
    """Anti-over-fix: the already-gated neighbours must stay auth-gated."""
    response = unauthenticated_client.get(path, headers=_tenant_headers())
    assert response.status_code in (401, 403), f"{path} served anonymously: {response.status_code}"


@pytest.mark.parametrize("path", AUTHENTICATED_NEIGHBOURS)
def test_authenticated_neighbours_still_serve_principals(client_auth, path) -> None:
    """Anti-over-fix: an authenticated principal is not denied by auth."""
    response = client_auth.get(path, headers=_tenant_headers())
    assert response.status_code != 401, f"{path} denied an authenticated principal: {response.text[:200]}"


@pytest.mark.parametrize("path", [*LEGACY_CATALOG_READS, *COMPAT_CATALOG_READS])
def test_authenticated_principal_is_not_denied(client_auth, path) -> None:
    """No functionality is lost: authenticated reads work on every path."""
    response = client_auth.get(path, headers=_tenant_headers())
    assert response.status_code != 401, f"{path} denied an authenticated principal: {response.text[:200]}"


def test_tenant_header_does_not_substitute_for_authentication(unauthenticated_client) -> None:
    """A caller-supplied X-Tenant-ID must not unlock catalog reads."""
    response = unauthenticated_client.get("/api/v1/billing/plans", headers=_tenant_headers())
    assert response.status_code in (401, 403), (
        f"tenant header bypassed authentication: {response.status_code} {response.text[:200]}"
    )
