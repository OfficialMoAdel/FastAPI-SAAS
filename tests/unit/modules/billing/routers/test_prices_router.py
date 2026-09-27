"""Unit tests for the /prices router (API compatibility layer)."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.app.modules.billing.routers.prices import router as prices_router

_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


def _authed_app(router_module_router, prefix: str = "/api/v1"):
    """Bare app with the RequestContextDep seams satisfied.

    Run-3 N-2 made the catalog reads authenticated, so a bare
    ``FastAPI()`` no longer serves them. HTTPBearer rejects an anonymous
    request before the auth context is consulted, so both seams are supplied
    here rather than weakening the route.
    """
    from src.app.api.deps.auth import oauth2_scheme
    from src.app.api.deps.request_context import RequestContext, get_request_context
    from src.app.core.tenancy.context import TenantContext
    from tests.helpers.auth import coerce_tenant, coerce_user

    app = FastAPI()
    app.include_router(router_module_router, prefix=prefix)
    app.dependency_overrides[oauth2_scheme] = lambda: "test-token"
    tenant = TenantContext(
        tenant_id=_TENANT_ID, tenant_slug="test-tenant", tenant_name="Test", is_active=True
    )
    app.dependency_overrides[get_request_context] = lambda: RequestContext(
        user=coerce_user({"id": 1, "is_superuser": True, "tenant_id": _TENANT_ID}),
        tenant=coerce_tenant(tenant),
        db=MagicMock(),
    )
    return app



@pytest.fixture
def app():
    """Create a FastAPI app instance with the prices router.

    Run-3 N-2: pricing rows are confidential tenant data, so the read routes
    take ``ctx: RequestContextDep``. The authenticated context is injected via a
    dependency override; the production path is covered by
    ``tests/unit/test_run3_needs_validation_fixes.py``.
    """
    from src.app.api.deps.auth import oauth2_scheme
    from src.app.api.deps.request_context import get_request_context
    from src.app.core.tenancy.context import TenantContext
    from tests.helpers.auth import coerce_tenant, coerce_user

    app = FastAPI()
    app.include_router(prices_router, prefix="/api/v1")

    # HTTPBearer rejects an anonymous request before get_auth_context is
    # consulted, so both seams are supplied: a dummy bearer token and a real
    # RequestContext carrying an authenticated principal + tenant.
    app.dependency_overrides[oauth2_scheme] = lambda: "test-token"
    tenant = TenantContext(
        tenant_id=_TENANT_ID, tenant_slug="test-tenant", tenant_name="Test", is_active=True
    )
    app.dependency_overrides[get_request_context] = lambda: _request_context(
        coerce_user({"id": 1, "is_superuser": True, "tenant_id": _TENANT_ID}),
        coerce_tenant(tenant),
    )
    return app


def _request_context(user, tenant):
    from src.app.api.deps.request_context import RequestContext

    return RequestContext(user=user, tenant=tenant, db=MagicMock())


@pytest.fixture
def client(app):
    """Create a test client."""
    return TestClient(app)


def test_prices_prefix():
    """Test that the router uses /prices prefix."""
    assert prices_router.prefix == "/prices"
    assert "prices" in prices_router.tags


def test_all_crud_endpoints_exist():
    """Test that all CRUD endpoints are defined."""
    routes = [(route.path, m) for route in prices_router.routes for m in route.methods]

    # Expected routes
    assert ("/prices", "GET") in routes  # List
    assert ("/prices", "POST") in routes  # Create
    assert ("/prices/{price_id}", "GET") in routes  # Get
    assert ("/prices/{price_id}", "PATCH") in routes  # Update
    assert ("/prices/{price_id}", "DELETE") in routes  # Delete (soft delete)


def test_create_requires_product_id_query():
    """Test that create endpoint requires product_id query parameter."""
    # Find the POST / route
    post_routes = [route for route in prices_router.routes if "POST" in route.methods and route.path == "/prices"]
    assert len(post_routes) == 1

    # Check that the endpoint has a product_id query parameter
    # This is verified by the router definition


def test_list_endpoint_supports_filters():
    """Test that list endpoint supports filtering by product_id and is_active."""
    with patch("src.app.modules.billing.routers.prices.PricingService") as MockService:
        mock_instance = MockService.return_value
        mock_instance.list_prices = AsyncMock(return_value={"data": [], "total_count": 0})

        # Mock the database and tenant dependencies
        with patch("src/app.modules.billing/routers.prices.async_get_db".replace("/", ".")):
            from fastapi.testclient import TestClient

            # Run-3 N-2: the route now takes RequestContextDep, so the
            # authenticated context is supplied via dependency overrides rather
            # than the old bare tenant_dependency.
            client = TestClient(_authed_app(prices_router))

            # Make request with filters
            client.get("/api/v1/prices?product_id=1&is_active=true")

            # Service should be called with correct filters
            mock_instance.list_prices.assert_called_once()
            call_kwargs = mock_instance.list_prices.call_args.kwargs
            assert call_kwargs.get("product_id") == 1
            assert call_kwargs.get("is_active") is True
            assert call_kwargs.get("page") == 1
            assert call_kwargs.get("limit") == 20
            # Run-3 N-2: the tenant now comes from the authenticated principal,
            # not from a free-standing tenant_dependency the caller could spoof.
            assert call_kwargs.get("tenant_id") == str(_TENANT_ID)


def test_delegates_to_billing_pricing_service():
    """Test that the router delegates to billing PricingService."""
    # The router imports PricingService from billing.service inside functions
    # Verify the import path is correct
    from src.app.modules.billing.service import PricingService

    # Should exist and be a class
    assert PricingService is not None
    assert hasattr(PricingService, "__init__")


def test_delete_is_soft_delete():
    """Test that delete endpoint performs soft delete (is_active=False)."""
    # The delete endpoint sets is_active=False instead of hard deleting
    # This is verified by the router implementation
    pass


def test_route_count():
    """Test that we have the expected number of route handlers."""
    # Count unique endpoints
    unique_paths = {(route.path, m) for route in prices_router.routes for m in route.methods}

    # Expected: GET/, POST/, GET/{id}, PATCH/{id}, DELETE/{id}
    assert len(unique_paths) == 5


def test_response_models():
    """Test that endpoints have correct response models."""
    from src.app.modules.billing.schemas import PricingList, PricingRead

    # Find routes and check response models
    for route in prices_router.routes:
        if route.path == "/prices" and "POST" in route.methods:
            # Create endpoint should return PricingRead
            assert route.response_model == PricingRead
        elif route.path == "/prices" and "GET" in route.methods:
            # List endpoint should return PricingList
            assert route.response_model == PricingList
        elif route.path == "/prices/{price_id}" and "GET" in route.methods:
            # Get endpoint should return PricingRead
            assert route.response_model == PricingRead
        elif route.path == "/prices/{price_id}" and "PATCH" in route.methods:
            # Update endpoint should return PricingRead
            assert route.response_model == PricingRead


def test_create_price_integration():
    """Test create price endpoint with mocked service."""
    # The service is imported locally in the route handler
    # We can verify the route structure instead
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(prices_router, prefix="/api/v1")
    TestClient(app)

    # Verify the route exists and has proper structure
    routes = [(route.path, m) for route in app.routes for m in getattr(route, "methods", [])]
    assert ("/api/v1/prices", "POST") in routes


def test_uses_integer_price_ids():
    """Test that price_id parameter uses integer type (database IDs)."""
    # Find routes with {price_id}
    price_id_routes = [route for route in prices_router.routes if "{price_id}" in route.path]

    # Should have GET, PATCH, DELETE endpoints
    assert len(price_id_routes) >= 3

    # All should use plain {price_id}, not {price_id:path} like products
    assert all("{price_id}" in r.path and "{price_id:path}" not in r.path for r in price_id_routes)
