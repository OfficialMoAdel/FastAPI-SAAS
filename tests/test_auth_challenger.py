"""
Verification tests for FastAPI SaaS Auth optimizations.
This file is written by teamwork_preview_challenger to verify:
1. DB lookup for user is exactly 1 when using get_current_user + requires_permission.
2. Duplicate JWT decodes are eliminated.
3. Superuser from Tenant A accessing Tenant B permission-protected endpoint is blocked (403).
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import jwt
import pytest
from httpx import ASGITransport, AsyncClient

from src.app.core.security import create_access_token

MOCK_TENANT_A_ID = UUID("00000000-0000-0000-0000-00000000000a")
MOCK_TENANT_B_ID = UUID("00000000-0000-0000-0000-00000000000b")

@asynccontextmanager
async def mock_get_redis_client():
    mock_client = AsyncMock()
    mock_client.exists = AsyncMock(return_value=0)
    mock_client.get = AsyncMock(return_value=None)
    yield mock_client

@pytest.mark.asyncio
async def test_auth_single_db_lookup_and_single_jwt_decode(get_test_app) -> None:
    """
    Verify that calling an endpoint protected by both get_current_user
    and requires_permission performs exactly 1 database lookup for the user
    and exactly 1 JWT decode.
    """
    app = get_test_app

    # Create mock user
    mock_user = {
        "id": 1,
        "email": "test@tenant-a.com",
        "username": "testuser",
        "tenant_id": MOCK_TENANT_A_ID,
        "is_active": True,
        "is_deleted": False,
        "token_version": 0,
        "is_superuser": False,
    }

    # Mock Tenant Middleware lookup to return Tenant A
    mock_tenant = MagicMock()
    mock_tenant.id = MOCK_TENANT_A_ID
    mock_tenant.slug = "tenant-a"
    mock_tenant.is_active = True

    async def mock_get_tenant_by_slug(*args, **kwargs):
        return mock_tenant

    # Mock User Service lookup
    mock_get_user_auth = AsyncMock(return_value=mock_user)

    # Mock permissions lookup
    mock_has_perm = AsyncMock(return_value=True)

    spy_jwt_decode = MagicMock(wraps=jwt.decode)

    # Generate token
    token = await create_access_token({"sub": "test@tenant-a.com", "tenant_id": str(MOCK_TENANT_A_ID)})

    # Setup redis mock
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=b"0")
    mock_redis.exists = AsyncMock(return_value=False)
    mock_redis.evalsha = AsyncMock(return_value=1)  # Below rate limit
    mock_redis.script_load = AsyncMock(return_value="fake_sha")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with (
            patch("src.app.middleware.tenant.TenantMiddleware._get_tenant_by_slug", new=mock_get_tenant_by_slug),
            patch("src.app.modules.users.service.UserService.get_user_auth_info", new=mock_get_user_auth),
            patch("src.app.core.rbac.dependencies.has_permission_cached", new=mock_has_perm),
            patch("jwt.decode", spy_jwt_decode),
            # Mock redis cache
            patch("src.app.core.utils.cache.client", mock_redis),
            patch("src.app.modules.security.service.get_redis_client", new=mock_get_redis_client),
            patch("src.app.core.security.token_blacklist_cache.is_token_blacklisted", AsyncMock(return_value=False)),
            patch("src.app.core.security.is_token_blacklisted", AsyncMock(return_value=False)),
        ):
            # Send GET request to /api/v1/audit-logs (protected by get_current_user and requires_permission)
            response = await client.get(
                "/api/v1/audit-logs",
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Tenant-ID": "tenant-a",
                }
            )

    # Assert request was successful (200 OK or 404 depending on db query inside endpoint, but auth should pass)
    print("Response status:", response.status_code)
    print("Response JSON:", response.text)
    assert response.status_code in (200, 404), f"Auth failed with status {response.status_code}: {response.text}"

    # Verify database lookup for user was called exactly once
    assert mock_get_user_auth.call_count == 1, f"Expected exactly 1 user DB lookup, got {mock_get_user_auth.call_count}"

    # Verify JWT decode was called exactly once (cached in request.state)
    assert spy_jwt_decode.call_count == 1, f"Expected exactly 1 JWT decode, got {spy_jwt_decode.call_count}"


@pytest.mark.asyncio
async def test_superuser_tenant_isolation_blocked(get_test_app) -> None:
    """
    Verify that a superuser from Tenant A attempting to access a
    permission-protected endpoint under Tenant B is blocked with 403 Forbidden.
    """
    app = get_test_app

    # Create mock superuser from Tenant A
    mock_superuser = {
        "id": 2,
        "email": "admin@tenant-a.com",
        "username": "adminuser",
        "tenant_id": MOCK_TENANT_A_ID,
        "is_active": True,
        "is_deleted": False,
        "token_version": 0,
        "is_superuser": True,
    }

    # Mock Tenant Middleware lookup to return Tenant B
    mock_tenant_b = MagicMock()
    mock_tenant_b.id = MOCK_TENANT_B_ID
    mock_tenant_b.slug = "tenant-b"
    mock_tenant_b.is_active = True

    async def mock_get_tenant_by_slug(*args, **kwargs):
        # The first arg is self, the second is slug
        if len(args) > 1 and args[1] == "tenant-b":
            return mock_tenant_b
        if "slug" in kwargs and kwargs["slug"] == "tenant-b":
            return mock_tenant_b
        # Fallback if slug is passed as first arg in directly mocking context
        if len(args) == 1 and args[0] == "tenant-b":
            return mock_tenant_b
        return None

    # Mock User Service lookup
    mock_get_user_auth = AsyncMock(return_value=mock_superuser)

    # Generate token (claims user belongs to Tenant A)
    token = await create_access_token({"sub": "admin@tenant-a.com", "tenant_id": str(MOCK_TENANT_A_ID)})

    # Setup redis mock
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=b"0")
    mock_redis.exists = AsyncMock(return_value=False)
    mock_redis.evalsha = AsyncMock(return_value=1)  # Below rate limit
    mock_redis.script_load = AsyncMock(return_value="fake_sha")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with (
            patch("src.app.middleware.tenant.TenantMiddleware._get_tenant_by_slug", new=mock_get_tenant_by_slug),
            patch("src.app.modules.users.service.UserService.get_user_auth_info", new=mock_get_user_auth),
            # Mock redis cache
            patch("src.app.core.utils.cache.client", mock_redis),
            patch("src.app.modules.security.service.get_redis_client", new=mock_get_redis_client),
            patch("src.app.core.security.token_blacklist_cache.is_token_blacklisted", AsyncMock(return_value=False)),
            patch("src.app.core.security.is_token_blacklisted", AsyncMock(return_value=False)),
        ):
            # Send GET request to Tenant B endpoint (/api/v1/audit-logs) with Tenant A's token
            response = await client.get(
                "/api/v1/audit-logs",
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Tenant-ID": "tenant-b",
                }
            )

    # Assert response is 403 Forbidden due to tenant mismatch
    print("Superuser tenant mismatch response status:", response.status_code)
    print("Superuser tenant mismatch response JSON:", response.text)
    assert response.status_code == 403, f"Expected 403 Forbidden, got {response.status_code}: {response.text}"
    assert "tenant" in response.json().get("detail", "").lower()
