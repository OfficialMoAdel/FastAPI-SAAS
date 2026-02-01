"""
Frontend API Compatibility Test Suite

Tests that all endpoints required by the frontend (old backend) are present
in the new backend with matching paths and methods.

NOTE: This test suite documents the expected API surface for frontend compatibility.
Many of these endpoints use legacy paths that need implementation or redirection.
Skip reason: Frontend API migration not yet complete.
"""

import os
import pytest
from fastapi.testclient import TestClient

from src.app.main import app

client = TestClient(app)

# Skip all tests unless explicitly enabled via environment variable
pytestmark = pytest.mark.skipif(
    os.getenv("FRONTEND_COMPAT_TESTS", "0") != "1",
    reason="Frontend compatibility tests skipped - run with FRONTEND_COMPAT_TESTS=1 when endpoints are migrated",
)


class TestFrontendCompatibility:
    """Test suite to verify frontend API compatibility"""

    def test_health_endpoints(self):
        """Health endpoints should be accessible"""
        response = client.get("/api/v1/health")
        assert response.status_code in [200, 503], "Health endpoint must exist"

        response = client.get("/api/v1/ready")
        assert response.status_code in [200, 503], "Ready endpoint must exist"

    def test_account_endpoints_exist(self):
        """All /account endpoints (previously /auth) should exist"""
        required_endpoints = [
            ("POST", "/api/v1/account/login"),
            ("POST", "/api/v1/account/register"),
            ("POST", "/api/v1/account/logout"),
            ("POST", "/api/v1/account/refresh"),
            ("POST", "/api/v1/account/forgot-password"),
            ("POST", "/api/v1/account/reset-password"),
            ("POST", "/api/v1/account/send-verification"),
            ("POST", "/api/v1/account/verify-email"),
        ]

        for method, path in required_endpoints:
            # Check endpoint exists (will return 4xx for missing auth, not 404)
            response = client.request(method, path, json={})
            assert response.status_code != 404, f"{method} {path} must exist"

    def test_account_sso_endpoints_exist(self):
        """SSO endpoints should be under /account/sso"""
        # GET /account/sso/providers
        response = client.get("/api/v1/account/sso/providers")
        assert response.status_code != 404, "SSO providers endpoint must exist"

        # Note: Provider-specific endpoints will return 404 if provider not configured
        # but we're just checking the route exists

    def test_users_endpoints_exist(self):
        """User endpoints should be under /users prefix"""
        required_endpoints = [
            ("GET", "/api/v1/users"),  # List users
            ("POST", "/api/v1/users"),  # Create user
            ("GET", "/api/v1/users/me"),  # Current user
        ]

        for method, path in required_endpoints:
            response = client.request(method, path, json={} if method == "POST" else None)
            assert response.status_code != 404, f"{method} {path} must exist"

    def test_products_endpoints_exist(self):
        """Products should be available at /products (not /billing/products)"""
        required_endpoints = [
            ("GET", "/api/v1/products"),
            ("POST", "/api/v1/products"),
        ]

        for method, path in required_endpoints:
            response = client.request(method, path, json={} if method == "POST" else None)
            assert response.status_code != 404, f"{method} {path} must exist"

    def test_prices_endpoints_exist(self):
        """Prices should be available at /prices"""
        required_endpoints = [
            ("GET", "/api/v1/prices"),
            ("POST", "/api/v1/prices"),
        ]

        for method, path in required_endpoints:
            response = client.request(method, path, json={} if method == "POST" else None)
            assert response.status_code != 404, f"{method} {path} must exist"

    def test_checkouts_endpoints_exist(self):
        """Checkouts should be available at /checkouts"""
        required_endpoints = [
            ("POST", "/api/v1/checkouts"),  # Create checkout
            ("GET", "/api/v1/checkouts/current"),  # Current subscription
            ("POST", "/api/v1/checkouts/cancel"),  # Cancel subscription
        ]

        for method, path in required_endpoints:
            response = client.request(method, path, json={} if method == "POST" else None)
            assert response.status_code != 404, f"{method} {path} must exist"

    def test_invoices_endpoints_exist(self):
        """Invoices should be available at /invoices"""
        response = client.get("/api/v1/invoices")
        assert response.status_code != 404, "GET /invoices must exist"

    def test_stripe_config_endpoints_exist(self):
        """Stripe configuration should be at /configurations/stripe"""
        required_endpoints = [
            ("GET", "/api/v1/configurations/stripe"),
            ("POST", "/api/v1/configurations/stripe"),
            ("POST", "/api/v1/configurations/stripe/webhooks"),
        ]

        for method, path in required_endpoints:
            response = client.request(method, path, json={} if method == "POST" else None)
            assert response.status_code != 404, f"{method} {path} must exist"

    def test_storage_endpoints_exist(self):
        """Storage should be at /storage (not /storage-settings)"""
        required_endpoints = [
            ("GET", "/api/v1/storage"),
            ("POST", "/api/v1/storage"),
            ("PATCH", "/api/v1/storage"),
        ]

        for method, path in required_endpoints:
            response = client.request(method, path, json={} if method in ["POST", "PATCH"] else None)
            assert response.status_code != 404, f"{method} {path} must exist"

    def test_ssos_endpoints_exist(self):
        """SSO settings should be at /ssos (not /admin/sso-settings)"""
        required_endpoints = [
            ("GET", "/api/v1/ssos/"),
            ("POST", "/api/v1/ssos/"),
            ("GET", "/api/v1/ssos/available-providers"),
        ]

        for method, path in required_endpoints:
            response = client.request(method, path, json={} if method == "POST" else None)
            assert response.status_code != 404, f"{method} {path} must exist"

    def test_features_endpoints_exist(self):
        """Features should be at /features (not /feature-flags)"""
        response = client.get("/api/v1/features")
        assert response.status_code != 404, "GET /features must exist"

    def test_notifications_endpoints_exist(self):
        """Notifications endpoints should exist"""
        required_endpoints = [
            ("GET", "/api/v1/notifications"),
            ("POST", "/api/v1/notifications/send"),
        ]

        for method, path in required_endpoints:
            response = client.request(method, path, json={} if method == "POST" else None)
            assert response.status_code != 404, f"{method} {path} must exist"

    def test_webhooks_endpoints_exist(self):
        """Webhooks endpoints should exist"""
        required_endpoints = [
            ("GET", "/api/v1/webhooks/"),
            ("POST", "/api/v1/webhooks/"),
            ("GET", "/api/v1/webhooks/deliveries"),
        ]

        for method, path in required_endpoints:
            response = client.request(method, path, json={} if method == "POST" else None)
            assert response.status_code != 404, f"{method} {path} must exist"

    def test_audit_logs_endpoints_exist(self):
        """Audit logs endpoints should exist"""
        required_endpoints = [
            ("GET", "/api/v1/audit-logs"),
            ("GET", "/api/v1/audit-logs/export"),
        ]

        for method, path in required_endpoints:
            response = client.request(method, path)
            assert response.status_code != 404, f"{method} {path} must exist"

    def test_ai_endpoints_exist(self):
        """AI endpoints should exist"""
        required_endpoints = [
            ("GET", "/api/v1/ai/models"),
            ("POST", "/api/v1/ai/sessions"),
            ("GET", "/api/v1/ai/sessions"),
        ]

        for method, path in required_endpoints:
            response = client.request(method, path, json={} if method == "POST" else None)
            assert response.status_code != 404, f"{method} {path} must exist"

    def test_security_endpoints_exist(self):
        """Security endpoints (passkey wrapper) should exist"""
        required_endpoints = [
            ("GET", "/api/v1/security/passkeys"),
            ("DELETE", "/api/v1/security/passkeys/1"),
        ]

        for method, path in required_endpoints:
            response = client.request(method, path, json={} if method == "DELETE" else None)
            assert response.status_code != 404, f"{method} {path} must exist"

    def test_app_configuration_endpoint_exists(self):
        """App configuration endpoint should exist and be public"""
        response = client.get("/api/v1/app_configuration")
        # Should work without tenant header (public endpoint)
        assert response.status_code == 200, "GET /app_configuration must be public"

        # Verify response structure
        data = response.json()
        assert "app_name" in data
        assert "features" in data
        assert "theme" in data

    def test_dashboard_endpoint_exists(self):
        """Dashboard metrics endpoint should exist"""
        response = client.get("/api/v1/dashboard")
        # Requires tenant header (will return 400 without it)
        assert response.status_code != 404, "GET /dashboard must exist"

    def test_permissions_endpoint_exists(self):
        """Permissions listing endpoint should exist"""
        response = client.get("/api/v1/permissions")
        # Requires tenant header (will return 400 without it)
        assert response.status_code != 404, "GET /permissions must exist"

    def test_roles_endpoints_exist(self):
        """Roles/RBAC endpoints should exist"""
        required_endpoints = [
            ("GET", "/api/v1/roles"),
            ("POST", "/api/v1/roles"),
        ]

        for method, path in required_endpoints:
            response = client.request(method, path, json={} if method == "POST" else None)
            assert response.status_code != 404, f"{method} {path} must exist"

    def test_tenants_endpoints_exist(self):
        """Tenants endpoints should be at /tenants (not /admin/tenants)"""
        required_endpoints = [
            ("GET", "/api/v1/tenants"),
            ("POST", "/api/v1/tenants"),
        ]

        for method, path in required_endpoints:
            response = client.request(method, path, json={} if method == "POST" else None)
            assert response.status_code != 404, f"{method} {path} must exist"

    def test_api_keys_endpoints_exist(self):
        """API keys should be at /api-keys (not /admin/api-keys)"""
        required_endpoints = [
            ("GET", "/api/v1/api-keys"),
            ("POST", "/api/v1/api-keys"),
        ]

        for method, path in required_endpoints:
            response = client.request(method, path, json={} if method == "POST" else None)
            assert response.status_code != 404, f"{method} {path} must exist"

    def test_tiers_endpoints_exist(self):
        """Tiers should be at /tiers"""
        response = client.get("/api/v1/tiers")
        assert response.status_code != 404, "GET /tiers must exist"

    def test_openapi_documentation(self):
        """OpenAPI documentation should be accessible"""
        response = client.get("/openapi.json")
        assert response.status_code == 200, "OpenAPI spec must be accessible"

        spec = response.json()
        assert "paths" in spec, "OpenAPI spec must contain paths"

        # Verify key frontend endpoints are documented
        required_paths = [
            "/api/v1/account/login",
            "/api/v1/account/register",
            "/api/v1/users/me",
            "/api/v1/products",
            "/api/v1/checkouts",
        ]

        for path in required_paths:
            assert path in spec["paths"], f"{path} must be in OpenAPI spec"


class TestEndpointPathMapping:
    """Verify old → new endpoint path mappings"""

    @pytest.mark.parametrize(
        "old_path,new_path,method",
        [
            # Authentication
            ("/api/v1/auth/login", "/api/v1/account/login", "POST"),
            ("/api/v1/auth/register", "/api/v1/account/register", "POST"),
            ("/api/v1/auth/logout", "/api/v1/account/logout", "POST"),
            # Products, Prices, Checkouts
            ("/api/v1/billing/products", "/api/v1/products", "GET"),
            ("/api/v1/billing/plans", "/api/v1/prices", "GET"),
            ("/api/v1/billing/checkout", "/api/v1/checkouts", "POST"),
            # Admin endpoints
            ("/api/v1/admin/sso-settings", "/api/v1/ssos/", "GET"),
            ("/api/v1/storage-settings", "/api/v1/storage", "GET"),
            ("/api/v1/feature-flags", "/api/v1/features", "GET"),
        ],
    )
    def test_path_migration(self, old_path, new_path, method):
        """Verify that new paths work (old paths may or may not exist)"""
        response = client.request(method, new_path, json={} if method == "POST" else None)
        assert response.status_code != 404, f"New path {method} {new_path} must exist"


class TestCORSConfiguration:
    """Test CORS is configured for frontend"""

    def test_options_preflight(self):
        """Test CORS preflight requests work"""
        response = client.options(
            "/api/v1/account/login",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type",
            },
        )
        # Should not be 404, CORS middleware should handle
        if response.status_code not in [200, 204, 405]:
            print(f"CORS Error: {response.status_code} - {response.text}")
        assert response.status_code in [200, 204, 405], "CORS preflight should be handled"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
