import os
import secrets
from collections.abc import Callable, Generator
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest
from cryptography.fernet import Fernet
from faker import Faker
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm.session import Session

from src.app.core.config import settings

# Ensure encryption key is set for tests - must be a valid Fernet key
_test_encryption_key = os.getenv("TEST_ENCRYPTION_KEY")
if not _test_encryption_key:
    _test_encryption_key = Fernet.generate_key().decode()
settings.ENCRYPTION_KEY = SecretStr(_test_encryption_key)

from src.app.main import app

DATABASE_URI = settings.POSTGRES_URI
DATABASE_PREFIX = settings.POSTGRES_SYNC_PREFIX

sync_engine = create_engine(DATABASE_PREFIX + DATABASE_URI)
local_session = sessionmaker(autocommit=False, autoflush=False, bind=sync_engine)

from src.app.core.db.database import Base
from src.app.modules.tenants.models import Tenant
from src.app.modules.auth.models import EmailVerificationToken, PasswordResetToken

# Import all models to ensure Base.metadata is fully populated for drop_all/create_all

fake = Faker()


@pytest.fixture(scope="session", autouse=True)
def setup_db():
    """Ensure fresh database schema for tests."""
    try:
        Base.metadata.drop_all(bind=sync_engine)
    except Exception as e:
        print(f"Warning: drop_all failed: {e}")
    Base.metadata.create_all(bind=sync_engine)
    # Optional: drop after tests
    # Base.metadata.drop_all(bind=sync_engine)



from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Setup async engine for tests (same DB as sync engine)
test_async_engine = create_async_engine(
    settings.POSTGRES_ASYNC_PREFIX + settings.POSTGRES_URI,
    echo=False,
    pool_size=5,
    max_overflow=5,
    pool_pre_ping=True
)
TestingSessionLocal = async_sessionmaker(bind=test_async_engine, class_=AsyncSession, expire_on_commit=False)

async def override_get_db() -> Generator[AsyncSession, None, None]:
    """Override for async_get_db dependency using test async engine."""
    async with TestingSessionLocal() as session:
        yield session

@pytest.fixture
def client() -> Generator[TestClient, Any, None]:
    # Patch create_tables and warm_caches to avoid using the global async engine which causes loop conflicts
    # Tables are already created by the setup_db fixture
    # Patch rate_limiter.check_rate_limit to return (False, 0) tuple as expected by middleware
    # Patch TenantMiddleware._get_tenant_by_slug to avoid async DB calls (middleware runs before dependency override?)
    # Actually, middleware might use global engine.
    # TenantMiddleware uses `request.state.db`? Or `db` dependency?
    # Usually middleware might create its own session or rely on request scope.
    # If middleware uses `async_get_db`, it will use our override.

    # Create a fake tenant object for middleware
    fake_tenant = Mock()
    fake_tenant.id = "test-tenant-id"
    fake_tenant.slug = "test-tenant-integration"
    fake_tenant.is_active = True

    # Create a fake user for dependency override (must support both attribute and dict access)
    fake_user = Mock()
    fake_user.id = "test-user-id"
    fake_user.username = "testuser"
    fake_user.email = "test@example.com"
    fake_user.is_superuser = False
    fake_user.tier_id = "test-tier-id"
    # Support dict-style access as well
    fake_user.__getitem__ = lambda self, key: getattr(self, key)

    # Import dependencies to override
    from src.app.api.dependencies import get_current_tenant, get_current_user, get_optional_user
    from src.app.core.db.database import async_get_db

    # Override dependencies
    app.dependency_overrides[get_current_user] = lambda: fake_user
    app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
    app.dependency_overrides[get_optional_user] = lambda: fake_user
    app.dependency_overrides[async_get_db] = override_get_db


    # Create a fake notification for send_email mock with all required fields for Pydantic
    from datetime import UTC, datetime
    from uuid import uuid4
    fake_notification = Mock()
    fake_notification.id = uuid4()
    fake_notification.tenant_id = uuid4()
    fake_notification.user_id = uuid4()
    fake_notification.template_name = "welcome"
    fake_notification.to_email = "test@example.com"
    fake_notification.subject = "Welcome"
    fake_notification.status = "sent"
    fake_notification.sent_at = datetime.now(UTC)
    fake_notification.error_message = None
    fake_notification.created_at = datetime.now(UTC)

    with (
        patch("src.app.core.setup.create_tables", new_callable=AsyncMock),
        patch("src.app.core.utils.cache_warming.warm_caches", new_callable=AsyncMock),
        patch(
            "src.app.core.utils.rate_limit.rate_limiter.check_rate_limit",
            new_callable=AsyncMock,
            return_value=(False, 0),
        ),
        patch(
            "src.app.middleware.tenant.TenantMiddleware._get_tenant_by_slug",
            new_callable=AsyncMock,
            return_value=fake_tenant,
        ),
        patch(
            "src.app.modules.notifications.service.NotificationService.send_email",
            new_callable=AsyncMock,
            return_value=fake_notification,
        ),
    ):

        with TestClient(app) as _client:
            yield _client
    app.dependency_overrides = {}


@pytest.fixture
def db() -> Generator[Session, Any, None]:
    session = local_session()
    yield session
    session.close()


@pytest.fixture
def client_auth() -> Generator[TestClient, Any, None]:
    """Client fixture for authentication tests - doesn't mock DB dependencies.

    Use this for tests that need real database access for authentication,
    like login/lockout tests.
    """
    # Create a fake tenant object for middleware
    fake_tenant = Mock()
    fake_tenant.id = 1
    fake_tenant.slug = "test-tenant-integration"
    fake_tenant.is_active = True

    with (
        patch("src.app.core.setup.create_tables", new_callable=AsyncMock),
        patch("src.app.core.utils.cache_warming.warm_caches", new_callable=AsyncMock),
        patch(
            "src.app.core.utils.rate_limit.rate_limiter.check_rate_limit",
            new_callable=AsyncMock,
            return_value=(False, 0),
        ),
        patch(
            "src.app.middleware.tenant.TenantMiddleware._get_tenant_by_slug",
            new_callable=AsyncMock,
            return_value=fake_tenant,
        ),
    ):

        with TestClient(app) as _client:
            yield _client


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
        if not key:
            return None
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

    async def ping(self):
        """Simulate Redis ping for health checks."""
        return True

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
