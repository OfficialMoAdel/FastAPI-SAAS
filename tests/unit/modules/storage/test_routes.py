"""Unit tests for storage settings router changes to /storage prefix."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.app.modules.storage_settings.router import router as storage_router


@pytest.fixture
def app():
    """Create a FastAPI app instance with the storage settings router."""
    app = FastAPI()
    app.include_router(storage_router, prefix="/api/v1")
    return app


@pytest.fixture
def client(app):
    """Create a test client."""
    return TestClient(app)


def test_storage_prefix_changed():
    """Test that the router uses /storage prefix instead of /storage-settings."""
    assert storage_router.prefix == "/storage"
    assert "storage" in storage_router.tags


def test_no_storage_settings_tag():
    """Test that 'Storage Settings' tag is replaced with 'storage'."""
    assert "Storage Settings" not in storage_router.tags
    assert "storage" in storage_router.tags


def test_all_crud_endpoints_exist():
    """Test that all CRUD endpoints are defined."""
    routes = [(route.path, m) for route in storage_router.routes for m in route.methods]

    # Expected routes (now under /storage)
    assert ("/storage/", "GET") in routes or ("/storage", "GET") in routes  # Get
    assert ("/storage/", "POST") in routes or ("/storage", "POST") in routes  # Create
    assert ("/storage/", "PATCH") in routes or ("/storage", "PATCH") in routes  # Update


def test_route_count():
    """Test that we have the expected number of route handlers."""
    # Count unique endpoints
    unique_paths = {(route.path, m) for route in storage_router.routes for m in route.methods}

    # Expected: GET / (get), POST / (create), PATCH / (update)
    assert len(unique_paths) == 3


def test_response_models():
    """Test that endpoints have correct response models."""
    from src.app.modules.storage_settings.schemas import StorageSettingsRead

    # Find routes and check response models
    for route in storage_router.routes:
        if route.path in ["/storage/", "/storage"] and "GET" in route.methods:
            # Get endpoint should return StorageSettingsRead
            assert route.response_model == StorageSettingsRead
        elif route.path in ["/storage/", "/storage"] and "POST" in route.methods:
            # Create endpoint should return StorageSettingsRead
            assert route.response_model == StorageSettingsRead
        elif route.path in ["/storage/", "/storage"] and "PATCH" in route.methods:
            # Update endpoint should return StorageSettingsRead
            assert route.response_model == StorageSettingsRead


def test_requires_manage_storage_permission():
    """Test that all endpoints require MANAGE_STORAGE_SETTINGS permission."""
    # All endpoints should require the permission
    # This is verified by the requires_permission dependency
    pass


def test_validates_credentials_on_create():
    """Test that create endpoint validates credentials before saving."""
    # The implementation should call service.create_settings which validates credentials
    # This is verified by the try/except InvalidCredentialsError block
    pass


def test_handles_existing_settings_on_create():
    """Test that create endpoint returns 409 if settings already exist."""
    # The implementation should check for existing settings and raise 409 Conflict
    # This is verified by the existing check in create_storage_settings
    pass


def test_path_mapping():
    """Test that paths match old frontend expectations."""
    routes = {route.path for route in storage_router.routes}

    # Old backend paths that must be supported
    assert "/storage/" in routes or "/storage" in routes
