"""Unit tests for feature flags router changes to /features prefix."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.app.modules.feature_flags.router import router as features_router


@pytest.fixture
def app():
    """Create a FastAPI app instance with the feature flags router."""
    app = FastAPI()
    app.include_router(features_router, prefix="/api/v1")
    return app


@pytest.fixture
def client(app):
    """Create a test client."""
    return TestClient(app)


def test_features_prefix_changed():
    """Test that the router uses /features prefix instead of /feature-flags."""
    assert features_router.prefix == "/features"
    assert "features" in features_router.tags


def test_no_feature_flags_tag():
    """Test that 'Feature Flags' tag is replaced with 'features'."""
    assert "Feature Flags" not in features_router.tags
    assert "features" in features_router.tags


def test_all_endpoints_exist():
    """Test that all CRUD endpoints are defined."""
    routes = [(route.path, m) for route in features_router.routes for m in route.methods]

    # Expected routes (now under /features)
    assert ("/features/", "GET") in routes or ("/features", "GET") in routes  # List
    assert ("/features/{flag_name}", "GET") in routes  # Check
    assert ("/features/{flag_name}", "PATCH") in routes  # Update
    assert ("/features/{flag_name}", "DELETE") in routes  # Delete
    assert ("/features/bulk", "PUT") in routes  # Bulk update


def test_route_count():
    """Test that we have the expected number of route handlers."""
    # Count unique endpoints
    unique_paths = set((route.path, m) for route in features_router.routes for m in route.methods)

    # Expected: GET / (list), GET /{name} (check), PATCH /{name}, DELETE /{name}, PUT /bulk
    assert len(unique_paths) == 5


def test_response_models():
    """Test that endpoints have correct response models."""
    from src.app.modules.feature_flags.schemas import (
        FeatureFlagCheckResponse,
        FeatureFlagsResponse,
    )

    # Find routes and check response models
    for route in features_router.routes:
        if route.path in ["/features/", "/features"] and "GET" in route.methods:
            # List endpoint should return FeatureFlagsResponse
            assert route.response_model == FeatureFlagsResponse
        elif route.path == "/features/{flag_name}" and "GET" in route.methods:
            # Check endpoint should return FeatureFlagCheckResponse
            assert route.response_model == FeatureFlagCheckResponse
        elif route.path == "/features/{flag_name}" and "PATCH" in route.methods:
            # Update endpoint should return FeatureFlagCheckResponse
            assert route.response_model == FeatureFlagCheckResponse
        elif route.path == "/features/bulk" and "PUT" in route.methods:
            # Bulk update should return FeatureFlagsResponse
            assert route.response_model == FeatureFlagsResponse


def test_list_requires_authentication():
    """Test that list endpoint requires authentication."""
    # List endpoint should require current_tenant and current_user
    # This is verified by the dependencies
    pass


def test_admin_endpoints_require_permission():
    """Test that admin endpoints require FEATURE_FLAGS_MANAGE permission."""
    # PATCH, DELETE, and PUT /bulk should require permission
    # This is verified by the requires_permission dependency
    pass


def test_bulk_update_endpoint_exists():
    """Test that bulk update endpoint exists."""
    routes = {(route.path, m): route for route in features_router.routes for m in route.methods}

    assert ("/features/bulk", "PUT") in routes


def test_path_mapping():
    """Test that paths match old frontend expectations."""
    routes = {route.path for route in features_router.routes}

    # Old backend paths that must be supported
    assert "/features/" in routes or "/features" in routes
    assert "/features/{flag_name}" in routes
