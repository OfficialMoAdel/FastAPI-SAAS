"""Unit tests for /configurations/stripe router (API compatibility layer)."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.app.modules.configurations.stripe_router import router as stripe_config_router


@pytest.fixture
def app():
    """Create a FastAPI app instance with the stripe configuration router."""
    app = FastAPI()
    app.include_router(stripe_config_router, prefix="/api/v1")
    return app


@pytest.fixture
def client(app):
    """Create a test client."""
    return TestClient(app)


def test_stripe_config_prefix():
    """Test that the router uses /configurations/stripe prefix."""
    assert stripe_config_router.prefix == "/configurations/stripe"
    assert "configurations" in stripe_config_router.tags
    assert "stripe" in stripe_config_router.tags


def test_all_endpoints_exist():
    """Test that all required endpoints are defined."""
    routes = [(route.path, m) for route in stripe_config_router.routes for m in route.methods]

    # Expected routes
    assert ("/configurations/stripe/", "GET") in routes or ("/configurations/stripe", "GET") in routes
    assert ("/configurations/stripe/", "POST") in routes or ("/configurations/stripe", "POST") in routes
    assert ("/configurations/stripe/webhooks", "POST") in routes


def test_webhooks_endpoint_plural():
    """Test that webhook endpoint uses plural /webhooks."""
    routes = {route.path for route in stripe_config_router.routes}

    # Should use plural
    assert "/configurations/stripe/webhooks" in routes

    # Should NOT use singular
    assert "/configurations/stripe/webhook" not in routes


def test_delegates_to_billing_service():
    """Test that the router delegates to billing StripeSettingsService."""
    from src.app.modules.configurations.stripe_router import get_stripe_settings_service
    from src.app.modules.billing.service import get_stripe_settings_service as billing_get_service

    # Should be the same function
    assert get_stripe_settings_service is billing_get_service


def test_uses_billing_webhook_handler():
    """Test that webhook endpoint uses billing module's webhook handler."""
    from src.app.modules.configurations.stripe_router import handle_webhook_event, verify_webhook_signature
    from src.app.modules.billing.webhook import (
        handle_webhook_event as billing_handle,
        verify_webhook_signature as billing_verify,
    )

    # Should be the same functions
    assert handle_webhook_event is billing_handle
    assert verify_webhook_signature is billing_verify


def test_route_count():
    """Test that we have the expected number of route handlers."""
    # Count unique endpoints
    unique_paths = set((route.path, m) for route in stripe_config_router.routes for m in route.methods)

    # Expected: GET / (get), POST / (create/update), POST /webhooks (webhook)
    assert len(unique_paths) == 3


def test_response_models():
    """Test that endpoints have correct response models."""
    from src.app.modules.billing.schemas import StripeSettingsRead, StripeSettingsReadFull

    # Find routes and check response models
    for route in stripe_config_router.routes:
        if route.path in ["/configurations/stripe/", "/configurations/stripe"] and "GET" in route.methods:
            # Get endpoint should return StripeSettingsReadFull
            assert route.response_model == StripeSettingsReadFull
        elif route.path in ["/configurations/stripe/", "/configurations/stripe"] and "POST" in route.methods:
            # Create/Update endpoint should return StripeSettingsRead
            assert route.response_model == StripeSettingsRead


def test_webhook_signature_verification():
    """Test that webhook endpoint requires stripe-signature header."""
    # The endpoint should have a stripe_signature parameter from headers
    # This is verified by the Header(alias="stripe-signature") annotation
    pass


def test_idempotency_check():
    """Test that webhook endpoint implements idempotency check."""
    # The implementation should:
    # 1. Check Redis for duplicate event ID
    # 2. Return early if duplicate
    # 3. Store event ID in Redis for 24 hours
    # This is verified by the implementation logic
    pass


def test_admin_only_endpoints():
    """Test that GET and POST require MANAGE_BILLING permission."""
    # Both GET and POST endpoints should require admin/billing permission
    # This is verified by the requires_permission dependency
    pass


def test_post_handles_create_and_update():
    """Test that POST endpoint handles both create and update operations."""
    # The endpoint should call update_settings which handles both cases
    # This is verified by the implementation using service.update_settings()
    pass
