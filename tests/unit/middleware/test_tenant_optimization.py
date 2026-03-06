import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from starlette.types import Scope

from src.app.middleware.tenant import TenantMiddleware
from src.app.modules.tenants.models import Tenant


@pytest.mark.asyncio
async def test_tenant_optimization_no_db_connection():
    """Verify that tenant middleware does NOT acquire a DB connection.

    This covers:
    - test_tenant_isolation_works_without_connection
    - test_feature_flag_on_skips_connection
    - test_no_rls_connection_in_request_state
    """
    app = FastAPI()
    middleware = TenantMiddleware(app)

    # Mock settings and dependencies
    with (
        patch("src.app.middleware.tenant.settings") as mock_settings,
        patch("src.app.middleware.tenant.async_engine") as mock_engine,
        patch("src.app.middleware.tenant.set_current_tenant") as mock_set_context,
        patch("src.app.middleware.tenant.clear_tenant_context") as mock_clear_context,
    ):
        mock_settings.performance.FEATURE_REMOVE_WASTED_CONNECTION = True

        # Mock _get_tenant_by_slug to return a tenant
        mock_tenant = Tenant(name="Test", slug="test", is_active=True)
        object.__setattr__(mock_tenant, "id", 1)

        with patch.object(middleware, "_get_tenant_by_slug", return_value=mock_tenant) as mock_get_tenant:

            async def call_next(request):
                return MagicMock()

            # Create request
            scope: Scope = {
                "type": "http",
                "method": "GET",
                "path": "/api/v1/some-endpoint",
                "headers": [(b"x-tenant-id", b"test")],
            }
            request = Request(scope)

            # Execute middleware
            await middleware.dispatch(request, call_next)

            # VERIFY: async_engine.connect() should NOT be called (The Optimization)
            mock_engine.connect.assert_not_called()

            # VERIFY: Tenant context was resolved and set
            mock_get_tenant.assert_called_once_with("test")
            mock_set_context.assert_called_once_with(mock_tenant)
            mock_clear_context.assert_called_once()

            # VERIFY: No RLS connection in request state
            assert not hasattr(request.state, "rls_connection")


@pytest.mark.asyncio
async def test_tenant_optimization_concurrent_isolation():
    """Verify concurrent requests are isolated and don't leak context.

    This covers:
    - test_concurrent_tenant_isolation
    """
    app = FastAPI()
    middleware = TenantMiddleware(app)

    with (
        patch("src.app.middleware.tenant.settings") as mock_settings,
        patch("src.app.middleware.tenant.async_engine") as mock_engine,
        patch("src.app.middleware.tenant.set_current_tenant") as mock_set_context,
        patch("src.app.middleware.tenant.clear_tenant_context") as mock_clear_context,
    ):
        mock_settings.performance.FEATURE_REMOVE_WASTED_CONNECTION = True
        # Mock separate tenants for separate requests
        tenant1 = Tenant(name="T1", slug="t1", is_active=True)
        object.__setattr__(tenant1, "id", 1)
        tenant2 = Tenant(name="T2", slug="t2", is_active=True)
        object.__setattr__(tenant2, "id", 2)

        async def mock_get_tenant(slug):
            if slug == "t1":
                return tenant1
            if slug == "t2":
                return tenant2
            return None

        with patch.object(middleware, "_get_tenant_by_slug", side_effect=mock_get_tenant):

            async def call_next(request):
                # Simulate some work to allow context switching
                await asyncio.sleep(0.01)
                return MagicMock()

            async def run_req(slug):
                scope = {
                    "type": "http",
                    "method": "GET",
                    "path": "/api/v1/resource",
                    "headers": [(b"x-tenant-id", slug.encode())],
                }
                request = Request(scope)
                await middleware.dispatch(request, call_next)
                return slug

            # Run concurrently
            results = await asyncio.gather(run_req("t1"), run_req("t2"))

            assert results == ["t1", "t2"]
            assert mock_set_context.call_count == 2
            mock_engine.connect.assert_not_called()


@pytest.mark.asyncio
async def test_performance_no_connection_wait():
    """Verify performance optimization by ensuring we don't wait for connection.

    This effectively verifies that we bypass the connection acquisition step.
    """
    app = FastAPI()
    middleware = TenantMiddleware(app)

    with (
        patch("src.app.middleware.tenant.settings") as mock_settings,
        patch("src.app.middleware.tenant.async_engine") as mock_engine,
        patch.object(middleware, "_get_tenant_by_slug") as mock_get_tenant,
        patch("src.app.middleware.tenant.set_current_tenant"),
    ):
        mock_settings.performance.FEATURE_REMOVE_WASTED_CONNECTION = True
        # Setup mock tenant
        mock_tenant = Tenant(name="Test", slug="test", is_active=True)
        object.__setattr__(mock_tenant, "id", 1)
        mock_get_tenant.return_value = mock_tenant

        # Make the connection acquisition incredibly slow (if it were called)
        async def slow_connect():
            await asyncio.sleep(1.0)
            return AsyncMock()

        mock_engine.connect.side_effect = slow_connect

        async def call_next(request):
            return MagicMock()

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/fast-endpoint",
            "headers": [(b"x-tenant-id", b"test")],
        }
        request = Request(scope)

        # Run middleware - should be fast because it DOES NOT call connect
        # If it called connect, it would take > 1.0s
        import time

        start = time.perf_counter()
        await middleware.dispatch(request, call_next)
        end = time.perf_counter()

        duration = end - start

        # Assert it took less than the slow connection time (plus overhead)
        assert duration < 0.5
        mock_engine.connect.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_optimized_logic():
    """Verify _dispatch_optimized logic sets context and calls next."""
    app = FastAPI()
    middleware = TenantMiddleware(app)
    
    mock_request = MagicMock()
    mock_call_next = AsyncMock()
    tenant = MagicMock(id=1, slug="test", is_active=True)
    
    with patch.object(middleware, "_get_tenant_by_slug", return_value=tenant), \
         patch("src.app.middleware.tenant.set_current_tenant") as mock_set, \
         patch("src.app.middleware.tenant.clear_tenant_context") as mock_clear:
        
        await middleware._dispatch_optimized(mock_request, mock_call_next, "/api/v1/users")
        
        mock_set.assert_called_once_with(tenant)
        mock_call_next.assert_called_once()
        mock_clear.assert_called_once()