"""Unit tests for SSO route changes to /account/sso prefix."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.app.modules.sso.router import router as sso_router


@pytest.fixture
def app():
    """Create a FastAPI app instance with the SSO router."""
    app = FastAPI()
    app.include_router(sso_router, prefix="/api/v1")
    return app


@pytest.fixture
def client(app):
    """Create a test client."""
    return TestClient(app)


def test_sso_prefix_changed():
    """Test that the router uses /account/sso prefix instead of /auth/sso."""
    assert sso_router.prefix == "/account/sso"
    assert "sso" in sso_router.tags


def test_sso_routes_structure():
    """Test that all SSO routes are properly structured under /account/sso."""
    routes = [route.path for route in sso_router.routes]

    # Expected routes
    expected_routes = [
        "/account/sso/providers",
        "/account/sso/{provider}/authorize",
        "/account/sso/{provider}",  # Alias for backward compatibility
        "/account/sso/{provider}/callback",
        "/account/sso/{provider}/connect",
    ]

    for expected_route in expected_routes:
        assert expected_route in routes, f"Route {expected_route} not found in {routes}"


def test_backward_compatibility_alias_exists():
    """Test that GET /{provider} alias exists for backward compatibility."""
    # FastAPI can have multiple routes with the same path but different methods
    get_routes = [route for route in sso_router.routes if "GET" in route.methods]
    delete_routes = [route for route in sso_router.routes if "DELETE" in route.methods]

    # Check that both GET authorize endpoint exists
    get_paths = [route.path for route in get_routes]
    assert "/account/sso/{provider}/authorize" in get_paths
    assert "/account/sso/{provider}" in get_paths

    # Check that DELETE disconnect endpoint exists
    delete_paths = [route.path for route in delete_routes]
    assert "/account/sso/{provider}" in delete_paths


def test_initiate_sso_endpoint():
    """Test that the SSO initiation endpoint is properly configured."""
    routes = {(route.path, m): route for route in sso_router.routes for m in route.methods}

    # Both paths should support GET
    assert ("/account/sso/{provider}/authorize", "GET") in routes
    assert ("/account/sso/{provider}", "GET") in routes


def test_callback_endpoints():
    """Test that callback endpoints support both GET and POST."""
    routes = {(route.path, m): route for route in sso_router.routes for m in route.methods}

    # Callback should support both GET and POST
    assert ("/account/sso/{provider}/callback", "GET") in routes
    assert ("/account/sso/{provider}/callback", "POST") in routes


def test_additional_endpoints_preserved():
    """Test that additional endpoints (providers, connect, disconnect) are preserved."""
    routes = {(route.path, m): route for route in sso_router.routes for m in route.methods}

    # Check for additional endpoints
    assert ("/account/sso/providers", "GET") in routes
    assert ("/account/sso/{provider}/connect", "POST") in routes
    assert ("/account/sso/{provider}", "DELETE") in routes


def test_route_count():
    """Test that we have the expected number of route handlers."""
    # Count unique route handlers (not route registrations)
    route_handlers = {route.endpoint for route in sso_router.routes}

    # We should have:
    # 1. list_providers
    # 2. initiate_sso
    # 3. initiate_sso_alias (new)
    # 4. handle_callback (GET)
    # 5. handle_post_callback (POST)
    # 6. connect_provider
    # 7. disconnect_provider
    assert len(route_handlers) == 7
