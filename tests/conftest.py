from collections.abc import Callable, Generator
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest
from faker import Faker
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm.session import Session

from src.app.core.config import settings
from src.app.main import app

DATABASE_URI = settings.POSTGRES_URI
DATABASE_PREFIX = settings.POSTGRES_SYNC_PREFIX

sync_engine = create_engine(DATABASE_PREFIX + DATABASE_URI)
local_session = sessionmaker(autocommit=False, autoflush=False, bind=sync_engine)

from src.app.core.db.database import Base
from src.app.modules.tenants.models import Tenant

# Import all models to ensure Base.metadata is fully populated for drop_all/create_all
from src.app.modules.users.models import User  # noqa: F401
from src.app.modules.tiers.models import Tier  # noqa: F401
from src.app.modules.posts.models import Post  # noqa: F401
from src.app.modules.auth.models import PasswordResetToken  # noqa: F401
from src.app.modules.rbac.models import Role, Permission  # noqa: F401
from src.app.modules.rate_limits.models import RateLimit  # noqa: F401
from src.app.modules.webhooks.models import WebhookEndpoint, WebhookDelivery  # noqa: F401
from src.app.modules.notifications.models import Notification  # noqa: F401
from src.app.modules.api_keys.models import APIKey  # noqa: F401
from src.app.modules.audit_logs.models import AuditLog  # noqa: F401
from src.app.modules.feature_flags.models import FeatureFlag, FeatureFlagOverride  # noqa: F401
from src.app.core.db.token_blacklist import TokenBlacklist  # noqa: F401

fake = Faker()


@pytest.fixture(scope="session", autouse=True)
def setup_db():
    """Ensure fresh database schema for tests."""
    Base.metadata.drop_all(bind=sync_engine)
    Base.metadata.create_all(bind=sync_engine)
    yield
    # Optional: drop after tests
    # Base.metadata.drop_all(bind=sync_engine)


@pytest.fixture(scope="session")
def client() -> Generator[TestClient, Any, None]:
    with TestClient(app) as _client:
        yield _client
    app.dependency_overrides = {}
    sync_engine.dispose()


@pytest.fixture
def db() -> Generator[Session, Any, None]:
    session = local_session()
    yield session
    session.close()


def override_dependency(dependency: Callable[..., Any], mocked_response: Any) -> None:
    app.dependency_overrides[dependency] = lambda: mocked_response


@pytest.fixture
def mock_db():
    """Mock database session for unit tests."""
    return Mock(spec=AsyncSession)


class FakeRedis:
    def __init__(self):
        self.store = {}

    async def get(self, key):
        if not key: return None
        return self.store.get(key)

    async def set(self, key, value, ex=None, px=None, **kwargs):
        self.store[key] = value
        return True

    async def delete(self, *keys):
        count = 0
        for k in keys:
            if k in self.store:
                del self.store[k]
                count += 1
        return count

    async def incr(self, key):
        val = int(self.store.get(key, 0)) + 1
        self.store[key] = str(val)
        return val

    async def setex(self, key, seconds, value):
        """Set key with expiration (seconds ignored in mock)."""
        self.store[key] = value
        return True

    async def expire(self, key, seconds):
        return bool(key in self.store)

    async def ttl(self, key):
        return 60 if key in self.store else -2

    async def pttl(self, key):
        return 60000 if key in self.store else -2

    async def scan(self, cursor=0, match=None, count=None):
        import fnmatch
        if cursor != 0:
            return 0, []
        keys = list(self.store.keys())
        if match:
            keys = fnmatch.filter(keys, match)
        return 0, keys

    async def close(self):
        pass

    async def enqueue_job(self, function, *args, **kwargs):
        return Mock(job_id="job_mock_123")

@pytest.fixture(autouse=True)
def mock_redis():
    """Mock Redis client globally with in-memory state."""
    fake_redis = FakeRedis()

    with patch("src.app.core.setup.create_redis_cache_pool", new=AsyncMock()), \
         patch("src.app.core.setup.create_redis_queue_pool", new=AsyncMock()), \
         patch("src.app.core.setup.create_redis_rate_limit_pool", new=AsyncMock()), \
         patch("src.app.core.setup.close_redis_cache_pool", new=AsyncMock()), \
         patch("src.app.core.setup.close_redis_queue_pool", new=AsyncMock()), \
         patch("src.app.core.setup.close_redis_rate_limit_pool", new=AsyncMock()), \
         patch("src.app.core.utils.cache.client", new=fake_redis), \
         patch("src.app.core.utils.rate_limit.rate_limiter.client", new=fake_redis), \
         patch("src.app.core.utils.rate_limit.RateLimiter.get_client", return_value=fake_redis), \
         patch("src.app.core.utils.queue.pool", new=fake_redis):
        yield fake_redis


@pytest.fixture
def sample_user_data():
    """Generate sample user data for tests."""
    return {
        "name": fake.name(),
        "username": fake.user_name(),
        "email": fake.email(),
        "password": fake.password(),
    }


@pytest.fixture
def sample_user_read():
    """Generate a sample UserRead object."""
    from uuid6 import uuid7

    from src.app.schemas.user import UserRead

    return UserRead(
        id=1,
        uuid=uuid7(),
        name=fake.name(),
        username=fake.user_name(),
        email=fake.email(),
        profile_image_url=fake.image_url(),
        is_superuser=False,
        created_at=fake.date_time(),
        updated_at=fake.date_time(),
        tier_id=None,
    )


@pytest.fixture
def current_user_dict():
    """Mock current user from auth dependency."""
    return {
        "id": 1,
        "username": fake.user_name(),
        "email": fake.email(),
        "name": fake.name(),
        "is_superuser": False,
    }


@pytest.fixture
def tenant(db):
    """Create and return a sample tenant."""

    # Check if tenant already exists to avoid UniqueViolation
    existing = db.query(Tenant).filter(Tenant.slug == "test-tenant-integration").first()
    if existing:
        return existing

    tenant_data = {
        "name": "Test Tenant Integration",
        "slug": "test-tenant-integration",
        "is_active": True,
    }
    tenant = Tenant(**tenant_data)
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    return tenant


@pytest.fixture
def auth_headers(tenant):
    """Authentication headers for tenant context."""
    return {"X-Tenant-ID": tenant.slug}
