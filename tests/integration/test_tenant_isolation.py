import pytest
from unittest.mock import AsyncMock
from uuid6 import uuid7
from fastapi.testclient import TestClient

from src.app.main import app
from src.app.api.dependencies import get_current_user
from src.app.modules.users.models import User
from src.app.modules.tenants.models import Tenant
from src.app.modules.posts.models import Post

# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------

@pytest.fixture
def tenant_alpha(db):
    """Create Tenant Alpha."""
    tenant = db.query(Tenant).filter(Tenant.slug == "alpha").first()
    if not tenant:
        tenant = Tenant(name="Alpha Corp", slug="alpha", is_active=True)
        db.add(tenant)
        db.commit()
        db.refresh(tenant)
    return tenant

@pytest.fixture
def tenant_beta(db):
    """Create Tenant Beta."""
    tenant = db.query(Tenant).filter(Tenant.slug == "beta").first()
    if not tenant:
        tenant = Tenant(name="Beta Inc", slug="beta", is_active=True)
        db.add(tenant)
        db.commit()
        db.refresh(tenant)
    return tenant

@pytest.fixture
def user_alpha(db, tenant_alpha):
    """Create User in Alpha Corp."""
    user = db.query(User).filter(User.email == "alice@alpha.com", User.tenant_id == tenant_alpha.id).first()
    if not user:
        user = User(
            name="Alice Alpha",
            username="alice_alpha",
            email="alice@alpha.com",
            hashed_password="hashed_secret",
            tenant_id=tenant_alpha.id,
            uuid=uuid7(),
            is_deleted=False
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    return user

@pytest.fixture
def user_beta(db, tenant_beta):
    """Create User in Beta Inc."""
    user = db.query(User).filter(User.email == "bob@beta.com", User.tenant_id == tenant_beta.id).first()
    if not user:
        user = User(
            name="Bob Beta",
            username="bob_beta",
            email="bob@beta.com",
            hashed_password="hashed_secret",
            tenant_id=tenant_beta.id,
            uuid=uuid7(),
            is_deleted=False
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    return user

@pytest.fixture
def post_alpha(db, user_alpha, tenant_alpha):
    """Create Post for Alice in Alpha."""
    post = Post(
        title="Alpha Secrets",
        text="This is a secret post for Alpha.",
        tenant_id=tenant_alpha.id,
        created_by_user_id=user_alpha.id,
        uuid=uuid7(),
        is_deleted=False
    )
    db.add(post)
    db.commit()
    db.refresh(post)
    return post

@pytest.fixture
def post_beta(db, user_beta, tenant_beta):
    """Create Post for Bob in Beta."""
    post = Post(
        title="Beta Trade Secrets",
        text="This is unrelated data for Beta.",
        tenant_id=tenant_beta.id,
        created_by_user_id=user_beta.id,
        uuid=uuid7(),
        is_deleted=False
    )
    db.add(post)
    db.commit()
    db.refresh(post)
    return post

# -----------------------------------------------------------------------------
# Tests
# -----------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def mock_redis_setup():
    """Disable Redis for integration tests to avoid connection errors."""
    with pytest.MonkeyPatch.context() as mp:
        # Mock the setup functions to do nothing
        mp.setattr("src.app.core.setup.create_redis_cache_pool", AsyncMock())
        mp.setattr("src.app.core.setup.create_redis_queue_pool", AsyncMock())
        mp.setattr("src.app.core.setup.create_redis_rate_limit_pool", AsyncMock())
        # Mock the close functions
        mp.setattr("src.app.core.setup.close_redis_cache_pool", AsyncMock())
        mp.setattr("src.app.core.setup.close_redis_queue_pool", AsyncMock())
        mp.setattr("src.app.core.setup.close_redis_rate_limit_pool", AsyncMock())
        
        # Ensure client is None or Mock so decorators checks work/skip
        # For @cache decorator, it checks `if client is None: raise MissingClientError`
        # Wait, if client is None, @cache raises MissingClientError.
        # So we MUST provide a mock client to src.app.core.utils.cache.client
        
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=None)
        mock_client.set = AsyncMock()
        mock_client.scan = AsyncMock(return_value=(0, []))
        
        mp.setattr("src.app.core.utils.cache.client", mock_client)
        mp.setattr("src.app.core.utils.rate_limit.rate_limiter.client", mock_client)
        
        yield


def test_tenant_isolation_list_posts(client: TestClient, user_alpha: User, tenant_alpha, post_alpha, post_beta):
    """
    Verify that Alice (Tenant Alpha) can ONLY see Alpha posts.
    """
    app.dependency_overrides[get_current_user] = lambda: {
        "id": user_alpha.id,
        "username": user_alpha.username,
        "email": user_alpha.email,
        "is_superuser": False,
        "tenant_id": user_alpha.tenant_id,
        "tier_id": None
    }
    
    # Correct URL: /api/v1/{username}/posts
    response = client.get(
        f"/api/v1/{user_alpha.username}/posts",
        headers={"X-Tenant-ID": tenant_alpha.slug}
    )
    
    assert response.status_code == 200, f"Response: {response.text}"
    data = response.json()["data"]
    
    # Debug info
    titles = [p["title"] for p in data]
    print(f"Alice saw posts: {titles}")
    
    assert len(data) >= 1
    assert any(p["title"] == "Alpha Secrets" for p in data)
    assert not any(p["title"] == "Beta Trade Secrets" for p in data)


def test_cross_tenant_access_denied(client: TestClient, user_alpha: User, user_beta: User, tenant_alpha, post_beta):
    """
    Verify that Alice (Tenant Alpha) CANNOT access Bob's (Tenant Beta) posts.
    """
    app.dependency_overrides[get_current_user] = lambda: {
        "id": user_alpha.id,
        "username": user_alpha.username,
        "email": user_alpha.email,
        "is_superuser": False,
        "tenant_id": user_alpha.tenant_id,
        "tier_id": None
    }
    
    response = client.get(
        f"/api/v1/{user_beta.username}/posts",
        headers={"X-Tenant-ID": tenant_alpha.slug}
    )
    
    # Expectation: 403 Forbidden or 404 Not Found
    print(f"Cross-tenant request status: {response.status_code}")
    print(f"Cross-tenant response: {response.text}")
    
    assert response.status_code in [403, 404]

    if response.status_code == 200:
        data = response.json().get("data", [])
        assert len(data) == 0, "Data Leakage! Alice saw Bob's posts."

