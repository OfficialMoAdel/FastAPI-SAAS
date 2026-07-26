"""Unit tests for path_utils."""

from src.app.core.utils.path_utils import sanitize_path


def test_sanitize_path_strip_api_prefix():
    assert sanitize_path("/api/v1/users/list") == "users_list"
    assert sanitize_path("api/v2/products") == "products"


def test_sanitize_path_collapse_uuid():
    uuid_str = "123e4567-e89b-12d3-a456-426614174000"
    assert sanitize_path(f"/api/v1/users/{uuid_str}/profile") == "users_{}_profile"


def test_sanitize_path_collapse_integers():
    assert sanitize_path("/api/v1/items/456/details") == "items_{}_details"
    assert sanitize_path("/api/v1/orders/123") == "orders_{}"


def test_sanitize_path_basic():
    assert sanitize_path("/health") == "health"
    assert sanitize_path("/") == ""
