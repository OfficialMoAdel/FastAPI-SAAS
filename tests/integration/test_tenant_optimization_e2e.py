import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch
from src.app.main import app
from src.app.core.config import settings
from src.app.modules.tenants.models import Tenant
from src.app.modules.users.models import User
from src.app.core.security import get_password_hash


# Fixtures for this test file
@pytest.fixture
async def tenant_alpha(db_session):
    tenant = Tenant(name="Alpha", slug="alpha", is_active=True)
    db_session.add(tenant)
    await db_session.commit()
    await db_session.refresh(tenant)
    return tenant


@pytest.fixture
async def user_alpha(db_session, tenant_alpha):
    pw = await get_password_hash("password")
    user = User(
        email="alpha@test.com", 
        username="alpha", 
        hashed_password=pw, 
        tenant_id=tenant_alpha.id,
        name="Alpha User"
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.fixture
async def tenant_beta(db_session):
    tenant = Tenant(name="Beta", slug="beta", is_active=True)
    db_session.add(tenant)
    await db_session.commit()
    await db_session.refresh(tenant)
    return tenant


@pytest.fixture
async def user_beta(db_session, tenant_beta):
    pw = await get_password_hash("password")
    user = User(
        email="beta@test.com", 
        username="beta", 
        hashed_password=pw, 
        tenant_id=tenant_beta.id,
        name="Beta User"
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.mark.asyncio
async def test_tenant_optimization_e2e(db_session, tenant_alpha, user_alpha, tenant_beta, user_beta):
    """
    Verify tenant isolation works with FEATURE_REMOVE_WASTED_CONNECTION = True.
    """
    # Mock Redis manually since integration tests might skip the global mock
    from unittest.mock import AsyncMock, MagicMock
    fake_redis = AsyncMock()
    fake_redis.exists.return_value = False # Not blacklisted
    fake_redis.get.return_value = None
    fake_redis.setex = AsyncMock()
    fake_redis.set = AsyncMock()
    
    from src.app.core.utils import cache
    
    # Patch local_session in middleware to use our test db_session
    # This ensures the middleware sees the tenants we created in SQLite
    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__.return_value = db_session
    mock_session_factory.return_value.__aexit__.return_value = None

    # Patch the setting, Redis client, AND DB session factory
    with patch.object(settings, "FEATURE_REMOVE_WASTED_CONNECTION", True), \
         patch.object(cache, "client", fake_redis), \
         patch("src.app.middleware.tenant.local_session", mock_session_factory):
        
        # Set User-Agent to avoid Bad Bot detection
        headers = {"User-Agent": "E2ETest/1.0"}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=headers) as ac:
            from src.app.api.dependencies import get_current_user

            # 1. Login as Alpha
            # Override for User Alpha
            app.dependency_overrides[get_current_user] = lambda: user_alpha

            # Request
            response = await ac.get("/api/v1/users/me", headers={"X-Tenant-ID": tenant_alpha.slug})
            assert response.status_code == 200, f"Alpha failed: {response.text}"
            data = response.json()
            assert data["email"] == user_alpha.email
            assert data["tenant_id"] == tenant_alpha.id

            # 2. Login as Beta
            app.dependency_overrides[get_current_user] = lambda: user_beta

            # Request
            response = await ac.get("/api/v1/users/me", headers={"X-Tenant-ID": tenant_beta.slug})
            assert response.status_code == 200, f"Beta failed: {response.text}"
            data = response.json()
            assert data["email"] == user_beta.email
            assert data["tenant_id"] == tenant_beta.id

            # Clean up
            app.dependency_overrides = {}