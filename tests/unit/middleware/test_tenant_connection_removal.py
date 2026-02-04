import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI, Request
from starlette.types import Scope

from src.app.middleware.tenant import TenantMiddleware
from src.app.modules.tenants.models import Tenant
from src.app.core.config import settings


@pytest.mark.asyncio
async def test_tenant_middleware_connection_removal_enabled():
    """Test that database connection is NOT acquired when feature flag is enabled."""
    app = FastAPI()
    middleware = TenantMiddleware(app)

    # Mock settings
    with patch("src.app.middleware.tenant.settings") as mock_settings:
        mock_settings.performance.FEATURE_REMOVE_WASTED_CONNECTION = True
        # Mock other needed settings defaults
        mock_settings.TENANT_NEGATIVE_CACHE_TTL = 300

        # Mock async_engine
        with patch("src.app.middleware.tenant.async_engine") as mock_engine:
            mock_connection = AsyncMock()
            mock_engine.connect.return_value.__aenter__.return_value = mock_connection

            # Mock _get_tenant_by_slug to avoid DB/Cache calls
            mock_tenant = Tenant(name="Test", slug="test", is_active=True)
            object.__setattr__(mock_tenant, "id", 1)

            with patch.object(middleware, "_get_tenant_by_slug", return_value=mock_tenant) as mock_get_tenant:
                # Mock call_next
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

                # VERIFY: async_engine.connect() should NOT be called
                mock_engine.connect.assert_not_called()

                # VERIFY: Tenant context was resolved
                mock_get_tenant.assert_called_once_with("test")


@pytest.mark.asyncio
async def test_tenant_middleware_connection_removal_disabled():
    """Test that database connection IS acquired when feature flag is disabled."""
    app = FastAPI()
    middleware = TenantMiddleware(app)

    # Mock settings
    with patch("src.app.middleware.tenant.settings") as mock_settings:
        mock_settings.performance.FEATURE_REMOVE_WASTED_CONNECTION = False

        # Mock async_engine
        with patch("src.app.middleware.tenant.async_engine") as mock_engine:
            mock_connection = AsyncMock()
            mock_engine.connect.return_value.__aenter__.return_value = mock_connection

            # Mock _get_tenant_by_slug
            mock_tenant = Tenant(name="Test", slug="test", is_active=True)
            object.__setattr__(mock_tenant, "id", 1)

            with patch.object(middleware, "_get_tenant_by_slug", return_value=mock_tenant):
                # Mock call_next
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

                # VERIFY: async_engine.connect() SHOULD be called
                mock_engine.connect.assert_called()

                # VERIFY: RLS variables set
                mock_connection.execute.assert_called()
