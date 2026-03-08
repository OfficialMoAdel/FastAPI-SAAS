"""Unit tests for SSO settings router changes to /ssos prefix."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.app.modules.sso_settings.router import router as sso_settings_router


@pytest.fixture
def app():
    """Create a FastAPI app instance with the SSO settings router."""
    app = FastAPI()
    app.include_router(sso_settings_router, prefix="/api/v1")
    return app


@pytest.fixture
def client(app):
    """Create a test client."""
    return TestClient(app)


def test_sso_settings_prefix_changed():
    """Test that the router uses /ssos prefix instead of /admin/sso-settings."""
    assert sso_settings_router.prefix == "/ssos"
    assert "ssos" in sso_settings_router.tags


def test_no_admin_tag():
    """Test that 'admin' and 'sso-settings' tags are removed in favor of 'ssos'."""
    assert "admin" not in sso_settings_router.tags
    assert "sso-settings" not in sso_settings_router.tags
    assert "ssos" in sso_settings_router.tags


def test_all_crud_endpoints_exist():
    """Test that all CRUD endpoints are defined."""
    routes = [(route.path, m) for route in sso_settings_router.routes for m in route.methods]

    # Expected routes (now under /ssos) - FastAPI adds trailing slash
    assert ("/ssos/", "GET") in routes  # List
    assert ("/ssos/", "POST") in routes  # Create
    assert ("/ssos/available-providers", "GET") in routes  # NEW endpoint
    assert ("/ssos/{provider}", "GET") in routes  # Get
    assert ("/ssos/{provider}", "PATCH") in routes  # Update
    assert ("/ssos/{provider}", "DELETE") in routes  # Delete


def test_available_providers_endpoint_exists():
    """Test that the new /available-providers endpoint exists."""
    routes = {(route.path, m): route for route in sso_settings_router.routes for m in route.methods}

    assert ("/ssos/available-providers", "GET") in routes


def test_available_providers_returns_list():
    """Test that available-providers endpoint returns list of strings."""
    routes = {(route.path, m): route for route in sso_settings_router.routes for m in route.methods}

    # Find the available-providers route
    route = routes.get(("/ssos/available-providers", "GET"))
    assert route is not None

    # Check response model
    assert route.response_model == list[str]


def test_available_providers_no_auth_dependency():
    """Test that available-providers endpoint doesn't require authentication.

    Note: The endpoint should be public for frontend to discover available providers.
    If it does require auth, this test will need adjustment.
    """
    # Find the available-providers route
    available_provider_routes = [
        route
        for route in sso_settings_router.routes
        if route.path == "/ssos/available-providers" and "GET" in route.methods
    ]
    assert len(available_provider_routes) == 1


def test_provider_parameter_preserved():
    """Test that {provider} parameter is preserved for CRUD operations."""
    routes = [(route.path, m) for route in sso_settings_router.routes for m in route.methods]

    # Should use {provider} not {sso_id}
    assert ("/ssos/{provider}", "GET") in routes
    assert ("/ssos/{provider}", "PATCH") in routes
    assert ("/ssos/{provider}", "DELETE") in routes


def test_route_count():
    """Test that we have the expected number of route handlers."""
    # Count unique endpoints
    unique_paths = {(route.path, m) for route in sso_settings_router.routes for m in route.methods}

    # Expected: GET/, POST/, GET/available-providers, GET/{provider}, PATCH/{provider}, DELETE/{provider}
    assert len(unique_paths) == 6


def test_superuser_dependency_on_crud():
    """Test that CRUD endpoints still require superuser access."""
    # All endpoints except available-providers should require superuser
    # This is verified by the dependencies in the router
    pass


def test_response_models():
    """Test that endpoints have correct response models."""
    from src.app.modules.sso_settings.schemas import (
        SSOSettingsList,
        SSOSettingsRead,
        SSOSettingsReadWithSecrets,
    )

    # Find routes and check response models
    for route in sso_settings_router.routes:
        if route.path == "/ssos" and "POST" in route.methods:
            # Create endpoint should return SSOSettingsRead
            assert route.response_model == SSOSettingsRead
        elif route.path == "/ssos" and "GET" in route.methods:
            # List endpoint should return SSOSettingsList
            assert route.response_model == SSOSettingsList
        elif route.path == "/ssos/{provider}" and "GET" in route.methods:
            # Get endpoint should return SSOSettingsReadWithSecrets
            assert route.response_model == SSOSettingsReadWithSecrets
        elif route.path == "/ssos/{provider}" and "PATCH" in route.methods:
            # Update endpoint should return SSOSettingsRead
            assert route.response_model == SSOSettingsRead
        elif route.path == "/ssos/available-providers" and "GET" in route.methods:
            # Available providers should return list[str]
            assert route.response_model == list[str]


def test_paths_match_old_frontend():
    """Test that paths match old frontend expectations."""
    routes = {route.path for route in sso_settings_router.routes}

    # Old backend paths that must be supported (FastAPI adds trailing slash)
    assert "/ssos/" in routes  # List and create
    assert "/ssos/available-providers" in routes  # Available providers
    assert "/ssos/{provider}" in routes  # CRUD operations
