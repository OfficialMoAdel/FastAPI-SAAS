from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.app.core.security import get_password_hash
from src.app.main import app
from src.app.modules.tenants.models import Tenant
from src.app.modules.users.models import User


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
        email="alpha@test.com", username="alpha", hashed_password=pw, tenant_id=tenant_alpha.id, name="Alpha User"
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
    user = User(email="beta@test.com", username="beta", hashed_password=pw, tenant_id=tenant_beta.id, name="Beta User")
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

    from src.app.api.dependencies import async_get_db

    fake_redis = AsyncMock()
    fake_redis.exists.return_value = False  # Not blacklisted
    fake_redis.get.return_value = None
    fake_redis.setex = AsyncMock()
    fake_redis.set = AsyncMock()

    from src.app.core.utils import cache

    # Use dependency override approach (like conftest.py) instead of patching
    # This ensures our session is used by all code paths
    async def override_get_db():
        yield db_session

    # Create dict-based mock users to avoid lazy loading issues
    # These simulate what get_current_user returns
    alpha_user_dict = {
        "id": user_alpha.id,
        "username": user_alpha.username,
        "email": user_alpha.email,
        "tenant_id": user_alpha.tenant_id,
    }
    beta_user_dict = {
        "id": user_beta.id,
        "username": user_beta.username,
        "email": user_beta.email,
        "tenant_id": user_beta.tenant_id,
    }

    # Patch local_session in middleware to use our test db_session
    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__.return_value = db_session
    mock_session_factory.return_value.__aexit__.return_value = None

    # Patch the Redis client AND DB session factory
    with (
        patch.object(cache, "client", fake_redis),
        patch("src.app.middleware.tenant.local_session", mock_session_factory),
    ):
        # Set User-Agent to avoid Bad Bot detection
        headers = {"User-Agent": "E2ETest/1.0"}

        # Use dependency_overrides like conftest.py does
        app.dependency_overrides[async_get_db] = override_get_db

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=headers) as ac:
            from src.app.api.dependencies import get_current_user

            # 1. Login as Alpha - use dict for current_user
            app.dependency_overrides[get_current_user] = lambda: alpha_user_dict

            # Request
            response = await ac.get("/api/v1/users/me", headers={"X-Tenant-ID": tenant_alpha.slug})
            assert response.status_code == 200, f"Alpha failed: {response.text}"
            data = response.json()
            assert data["email"] == user_alpha.email
            assert data["tenant_id"] == str(tenant_alpha.id)  # Compare as string

            # 2. Login as Beta - use dict for current_user
            app.dependency_overrides[get_current_user] = lambda: beta_user_dict

            # Request
            response = await ac.get("/api/v1/users/me", headers={"X-Tenant-ID": tenant_beta.slug})
            assert response.status_code == 200, f"Beta failed: {response.text}"
            data = response.json()
            assert data["email"] == user_beta.email
            assert data["tenant_id"] == str(tenant_beta.id)  # Compare as string

            # Clean up
            app.dependency_overrides.clear()
