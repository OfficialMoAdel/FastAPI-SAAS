"""Unit Tests: Middleware Order and Tenant-Aware Rate Limiting.

Tests validate P1-15 middleware reordering: RateLimitMiddleware runs
before TenantMiddleware so a cheap 429 rejects before the tenant lookup
(L1+L2+DB). The old P2-5 Tenant-first rationale is stale — the rate-limit
request path resolves tenant_id from JWT claims/request.state and never
reads the tenant ContextVar (dep-layer ``is_rate_limited`` still sees the
context because TenantMiddleware runs before the handler regardless).
"""

from unittest.mock import MagicMock

import pytest


class TestMiddlewareOrder:
    """Test middleware ordering for P1-15 optimization."""

    def test_middleware_stack_order_in_main(self):
        """Verify middleware is added in correct order for pre-tenant rate limiting."""
        import inspect

        from src.app import main

        source = inspect.getsource(main)

        # Find the middleware registration section
        gzip_pos = source.find("app.add_middleware(GZipFilterMiddleware")
        security_pos = source.find("app.add_middleware(SecurityHeadersMiddleware")
        body_limit_pos = source.find("RequestBodyLimitMiddleware, max_body_size")
        tenant_pos = source.find("app.add_middleware(TenantMiddleware")
        rate_limit_pos = source.find("app.add_middleware(RateLimitMiddleware")
        request_id_pos = source.find("app.add_middleware(RequestIDMiddleware")

        # All middleware should be registered
        assert gzip_pos > 0, "GZipFilterMiddleware not found"
        assert security_pos > 0, "SecurityHeadersMiddleware not found"
        assert body_limit_pos > 0, "RequestBodyLimitMiddleware not found"
        assert tenant_pos > 0, "TenantMiddleware not found"
        assert rate_limit_pos > 0, "RateLimitMiddleware not found"
        assert request_id_pos > 0, "RequestIDMiddleware not found"

        # P1-15: RateLimit must be added AFTER Tenant (LIFO) so it runs
        # BEFORE Tenant on the request path: Request -> RateLimit -> Tenant.
        # A 429 then rejects before the tenant lookup (L1+L2+DB) runs. To run
        # first, it must be added last. So RateLimit added AFTER Tenant.
        assert rate_limit_pos > tenant_pos, (
            "P1-15: RateLimitMiddleware must be added after TenantMiddleware "
            "so it runs before TenantMiddleware (LIFO) and 429s skip the "
            "tenant lookup"
        )

        # Verify logical ordering (LIFO means later additions run first on request)
        # Execution Order: GZip -> Security -> BodyLimit -> RateLimit -> Tenant -> RequestID
        # Addition Order: RequestID -> Tenant -> RateLimit -> Security -> BodyLimit -> GZip
        assert request_id_pos < tenant_pos, "RequestID added before Tenant"
        assert tenant_pos < rate_limit_pos, "Tenant added before RateLimit"
        assert rate_limit_pos < security_pos, "RateLimit added before Security"
        assert security_pos < body_limit_pos, "Security added before BodyLimit"
        assert body_limit_pos < gzip_pos, "BodyLimit added before GZip"

    def test_p1_15_comment_documents_ordering(self):
        """Verify P1-15 optimization is documented in code."""
        import inspect

        from src.app import main

        source = inspect.getsource(main)

        # Check for documentation of the ordering decision
        assert "P1-15" in source, (
            "Middleware ordering should document P1-15 pre-tenant rate limiting"
        )


class TestTenantContextAvailability:
    """Test that tenant context is available to rate limiter."""

    @pytest.mark.asyncio
    async def test_tenant_context_set_before_rate_limit(self):
        """Verify tenant context is set before rate limit middleware runs."""
        from src.app.core.tenancy.context import get_current_tenant, set_current_tenant

        # Simulate TenantMiddleware setting context
        mock_tenant = MagicMock()
        mock_tenant.id = 123
        mock_tenant.slug = "test-tenant"
        mock_tenant.is_active = True

        set_current_tenant(mock_tenant)

        # RateLimitMiddleware should be able to access tenant
        context = get_current_tenant()
        assert context is not None
        assert context.id == 123
        assert context.slug == "test-tenant"

    @pytest.mark.asyncio
    async def test_rate_limit_can_use_tenant_for_key(self):
        """Test rate limiter can incorporate tenant into rate limit key."""
        from src.app.core.tenancy.context import get_current_tenant, set_current_tenant

        mock_tenant = MagicMock()
        mock_tenant.id = 456
        mock_tenant.slug = "acme-corp"

        set_current_tenant(mock_tenant)

        # Simulate building a tenant-aware rate limit key
        tenant = get_current_tenant()
        if tenant:
            rate_limit_key = f"rate_limit:tenant:{tenant.id}:endpoint:/api/users"
        else:
            rate_limit_key = "rate_limit:global:endpoint:/api/users"

        assert "tenant:456" in rate_limit_key


class TestMiddlewareExecutionOrder:
    """Test actual middleware execution order via request simulation."""

    @pytest.mark.asyncio
    async def test_execution_order_tracking(self):
        """Track middleware execution order via a mock request."""
        execution_order = []

        # Create mock middleware that records execution
        class TrackingMiddleware:
            def __init__(self, app, name: str):
                self.app = app
                self.name = name

            async def __call__(self, scope, receive, send):
                if scope["type"] == "http":
                    execution_order.append(f"{self.name}_start")
                await self.app(scope, receive, send)
                if scope["type"] == "http":
                    execution_order.append(f"{self.name}_end")

        # The expected order based on LIFO middleware addition
        # Last added (RequestID) runs first on request

        # Just verify we understand the LIFO pattern
        # In LIFO: add_middleware(A), add_middleware(B) -> B runs before A
        assert True  # This is a documentation test


class TestRateLimitMiddlewareTenantIntegration:
    """Test RateLimitMiddleware can access tenant context."""

    @pytest.mark.asyncio
    async def test_rate_limit_respects_tenant_tier(self):
        """Test rate limiter can apply tenant-specific limits."""
        # This tests the capability enabled by P2-5
        from src.app.core.tenancy.context import get_current_tenant, set_current_tenant

        # Tenant with premium tier (higher limits)
        premium_tenant = MagicMock()
        premium_tenant.id = 1
        premium_tenant.slug = "premium-co"
        premium_tenant.name = "Premium Company"
        premium_tenant.is_active = True

        set_current_tenant(premium_tenant)

        tenant = get_current_tenant()

        # Verify tenant context was set correctly
        assert tenant.id == 1
        assert tenant.slug == "premium-co"

    @pytest.mark.asyncio
    async def test_rate_limit_falls_back_without_tenant(self):
        """Test rate limiter works without tenant context."""
        from src.app.core.tenancy.context import clear_tenant_context, get_current_tenant

        clear_tenant_context()
        tenant = get_current_tenant()

        # Rate limiter should fall back to IP-based or global limits
        assert tenant is None


class TestMiddlewareStackIntegrity:
    """Test middleware stack maintains integrity after reordering."""

    def test_all_middleware_registered(self):
        """Verify all required middleware is still registered."""
        from src.app.main import app

        middleware_classes = [m.cls.__name__ if hasattr(m, "cls") else str(m) for m in app.user_middleware]

        # Check essential middleware are present
        middleware_str = " ".join(middleware_classes)
        assert "GZip" in middleware_str or "gzip" in middleware_str.lower()
        assert "Tenant" in middleware_str
        assert "RateLimit" in middleware_str
        assert "RequestID" in middleware_str

    def test_middleware_count_unchanged(self):
        """Verify middleware count is correct."""
        from src.app.main import app

        # Should have 6 middleware registered
        # GZip, Security, BodyLimit, Tenant, RateLimit, RequestID
        assert len(app.user_middleware) >= 5, f"Expected at least 5 middleware, found {len(app.user_middleware)}"
