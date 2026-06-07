"""Unit tests for the /products router (API compatibility layer)."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.app.modules.billing.routes.products import router as products_router


@pytest.fixture
def app():
    """Create a FastAPI app instance with the products router."""
    app = FastAPI()
    app.include_router(products_router, prefix="/api/v1")
    return app


@pytest.fixture
def client(app):
    """Create a test client."""
    return TestClient(app)


def test_products_prefix():
    """Test that the router uses /products prefix."""
    assert products_router.prefix == "/products"
    assert "products" in products_router.tags


def test_all_crud_endpoints_exist():
    """Test that all CRUD endpoints are defined."""
    routes = [(route.path, m) for route in products_router.routes for m in route.methods]

    # Expected routes (note: uses :path type for Stripe IDs)
    assert ("/products", "GET") in routes  # List
    assert ("/products", "POST") in routes  # Create
    assert ("/products/{product_id:int}", "GET") in routes  # Get
    assert ("/products/{product_id:int}", "PATCH") in routes  # Update
    assert ("/products/{product_id:int}", "DELETE") in routes  # Delete
    assert ("/products/{product_id:int}/pricing", "POST") in routes  # Add pricing


def test_path_parameter_for_stripe_ids():
    """Test that product_id uses :int type for integer IDs."""
    # Find the GET /{product_id:int} route
    get_routes = [route for route in products_router.routes if "GET" in route.methods]
    product_detail_routes = [r for r in get_routes if "{product_id:int}" in r.path]

    # Should have at least one route with {product_id:int}
    assert len(product_detail_routes) > 0

    # The path should contain the parameter with :int type
    assert any("{product_id:int}" in r.path for r in product_detail_routes)


def test_list_endpoint_pagination():
    """Test that list endpoint supports pagination parameters."""
    with patch("src.app.modules.billing.routes.products.get_product_service") as mock_get_service:
        mock_service = AsyncMock()
        import uuid
        from datetime import datetime

        mock_service.get_products = AsyncMock(
            return_value={
                "data": [
                    {
                        "id": 1,
                        "name": "Product 1",
                        "tenant_id": str(uuid.uuid4()),
                        "description": "Desc 1",
                        "stripe_product_id": "prod_1",
                        "is_active": True,
                        "created_at": datetime.now().isoformat(),
                    },
                    {
                        "id": 2,
                        "name": "Product 2",
                        "tenant_id": str(uuid.uuid4()),
                        "description": "Desc 2",
                        "stripe_product_id": "prod_2",
                        "is_active": True,
                        "created_at": datetime.now().isoformat(),
                    },
                ],
                "total_count": 2,
            }
        )
        mock_get_service.return_value = mock_service

        # Mock the database dependency
        with patch("src.app.modules.billing.routes.products.async_get_db"):
            from fastapi import FastAPI
            from fastapi.testclient import TestClient

            app = FastAPI()
            app.include_router(products_router, prefix="/api/v1")
            client = TestClient(app)

            # Make request with pagination
            client.get("/api/v1/products?page=1&per_page=20")

            # Service should be called with correct params
            mock_service.get_products.assert_called_once()
            call_kwargs = mock_service.get_products.call_args.kwargs
            assert call_kwargs["page"] == 1
            assert call_kwargs["limit"] == 20


def test_delegates_to_billing_service():
    """Test that the router delegates to billing ProductService."""
    # This is verified by the import in router.py
    from src.app.modules.billing.routes.products import get_product_service
    from src.app.modules.billing.service import get_product_service as billing_get_product_service

    # Should be the same function
    assert get_product_service is billing_get_product_service


def test_route_count():
    """Test that we have the expected number of route handlers."""
    # Count unique endpoints
    unique_paths = {(route.path, m) for route in products_router.routes for m in route.methods}

    # Expected: GET/, POST/, GET/{id}, PATCH/{id}, DELETE/{id}, POST/{id}/pricing
    assert len(unique_paths) == 6


def test_pricing_endpoint_exists():
    """Test that the pricing endpoint is available."""
    routes = {(route.path, m): route for route in products_router.routes for m in route.methods}

    assert ("/products/{product_id:int}/pricing", "POST") in routes


def test_response_models():
    """Test that endpoints have correct response models."""
    from src.app.modules.billing.schemas import PricingRead, ProductList, ProductRead

    # Find routes and check response models
    for route in products_router.routes:
        if route.path == "/products" and "POST" in route.methods:
            # Create endpoint should return ProductRead
            assert route.response_model == ProductRead
        elif route.path == "/products" and "GET" in route.methods:
            # List endpoint should return ProductList
            assert route.response_model == ProductList
        elif route.path == "/products/{product_id}" and "GET" in route.methods:
            # Get endpoint should return ProductRead
            assert route.response_model == ProductRead
        elif route.path == "/products/{product_id}/pricing" and "POST" in route.methods:
            # Add pricing should return PricingRead
            assert route.response_model == PricingRead
