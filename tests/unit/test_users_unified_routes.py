"""Unit tests for unified users router under /users prefix."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.app.modules.users.router import router as users_router


@pytest.fixture
def app():
    """Create a FastAPI app instance with the users router."""
    app = FastAPI()
    app.include_router(users_router, prefix="/api/v1")
    return app


@pytest.fixture
def client(app):
    """Create a test client."""
    return TestClient(app)


def test_users_prefix():
    """Test that the router uses /users prefix."""
    assert users_router.prefix == "/users"
    assert "users" in users_router.tags


def test_all_endpoints_under_users():
    """Test that all endpoints are now under /users prefix."""
    routes = [(route.path, m) for route in users_router.routes for m in route.methods]

    # All routes should start with /users
    for path, method in routes:
        assert path.startswith("/users"), f"Route {method} {path} doesn't start with /users"


def test_me_endpoint_no_trailing_slash():
    """Test that /me endpoint doesn't have trailing slash."""
    routes = {route.path for route in users_router.routes}

    assert "/users/me" in routes
    assert "/users/me/" not in routes


def test_crud_endpoints_exist():
    """Test that all CRUD endpoints exist."""
    routes = [(route.path, m) for route in users_router.routes for m in route.methods]

    # Core CRUD operations
    assert ("/users/", "GET") in routes or ("/users", "GET") in routes  # List
    assert ("/users/", "POST") in routes or ("/users", "POST") in routes  # Create
    assert ("/users/me", "GET") in routes  # Get current user
    assert ("/users/{user_id}", "GET") in routes  # Get by ID
    assert ("/users/{user_id}", "PATCH") in routes  # Update
    assert ("/users/{user_id}", "PUT") in routes  # Update (full, alias)
    assert ("/users/{user_id}", "DELETE") in routes  # Delete


def test_put_alias_exists():
    """Test that PUT endpoint exists as alias for PATCH."""
    routes = [(route.path, m) for route in users_router.routes for m in route.methods]

    # Both PATCH and PUT should exist for /{user_id}
    assert ("/users/{user_id}", "PATCH") in routes
    assert ("/users/{user_id}", "PUT") in routes


def test_user_id_parameter_not_username():
    """Test that endpoints use {user_id} not {username}."""
    routes = {route.path for route in users_router.routes}

    # Should use {user_id}
    assert "/users/{user_id}" in routes

    # Should NOT use {username}
    assert "/users/{username}" not in routes


def test_sub_endpoints_exist():
    """Test that user sub-endpoints exist."""
    routes = {(route.path, m): route for route in users_router.routes for m in route.methods}

    # Sub-endpoints
    assert ("/users/{user_id}/rate-limits", "GET") in routes  # Changed from rate_limits
    assert ("/users/{user_id}/tier", "GET") in routes
    assert ("/users/{user_id}/tier", "PATCH") in routes


def test_rate_limits_endpoint_uses_hyphen():
    """Test that rate-limits endpoint uses hyphen not underscore."""
    routes = {route.path for route in users_router.routes}

    # Should use hyphen
    assert "/users/{user_id}/rate-limits" in routes

    # Should NOT use underscore
    assert "/users/{user_id}/rate_limits" not in routes


def test_db_delete_endpoint_moved():
    """Test that hard delete endpoint is under /db/{user_id}."""
    routes = {(route.path, m): route for route in users_router.routes for m in route.methods}

    assert ("/users/db/{user_id}", "DELETE") in routes


def test_route_count():
    """Test that we have the expected number of route handlers."""
    # Count unique endpoints
    unique_paths = set((route.path, m) for route in users_router.routes for m in route.methods)

    # Expected: GET/, POST/, GET/me, GET/{id}, PATCH/{id}, PUT/{id}, DELETE/{id},
    #           DELETE/db/{id}, GET/{id}/rate-limits, GET/{id}/tier, PATCH/{id}/tier
    assert len(unique_paths) >= 11


def test_response_models():
    """Test that endpoints have correct response models."""
    from src.app.modules.users.schemas import UserRead, UserList

    # Find routes and check response models
    for route in users_router.routes:
        if route.path in ["/users/", "/users"] and "POST" in route.methods:
            # Create endpoint should return UserRead
            assert route.response_model == UserRead
        elif route.path in ["/users/", "/users"] and "GET" in route.methods:
            # List endpoint should return UserList
            assert route.response_model == UserList
        elif route.path == "/users/me" and "GET" in route.methods:
            # Me endpoint should return UserRead
            assert route.response_model == UserRead
        elif route.path == "/users/{user_id}" and "GET" in route.methods:
            # Get by ID should return UserRead
            assert route.response_model == UserRead
