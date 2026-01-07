import os
from collections.abc import AsyncGenerator, Callable, Generator
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest
from cryptography.fernet import Fernet
from faker import Faker
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import Engine, String, create_engine, event
from sqlalchemy.dialects.postgresql import ARRAY, BYTEA, INET, JSONB, UUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.expression import TextClause


@compiles(JSONB, "sqlite")
def compile_jsonb(type_, compiler, **kw):
    return compiler.visit_JSON(type_, **kw)


@compiles(UUID, "sqlite")
def compile_uuid(type_, compiler, **kw):
    return compiler.visit_VARCHAR(type_, **kw)


@compiles(BYTEA, "sqlite")
def compile_bytea(type_, compiler, **kw):
    return compiler.visit_large_binary(type_, **kw)


@compiles(INET, "sqlite")
def compile_inet(type_, compiler, **kw):
    return compiler.visit_VARCHAR(String(45), **kw)


@compiles(ARRAY, "sqlite")
def compile_array(type_, compiler, **kw):
    return compiler.visit_JSON(type_, **kw)


@compiles(TextClause, "sqlite")
def compile_text_clause(element, compiler, **kw):
    text = element.text
    if "current_timestamp(0)" in text:
        text = text.replace("current_timestamp(0)", "CURRENT_TIMESTAMP")
    return text


def gen_random_uuid():
    return str(uuid.uuid4())


@event.listens_for(Engine, "connect")
def do_connect(dbapi_connection, connection_record):
    try:
        dbapi_connection.create_function("gen_random_uuid", 0, gen_random_uuid)
    except Exception:
        pass
    try:
        # Enable Write-Ahead Logging (WAL) for better concurrency
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()
    except Exception:
        pass


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

# DATABASE_URI = settings.POSTGRES_URI
# DATABASE_PREFIX = settings.POSTGRES_SYNC_PREFIX
# Use test.db specifically for tests to avoid conflicts with sql_app.db
DATABASE_URI = "./test.db"
DATABASE_PREFIX = settings.SQLITE_SYNC_PREFIX

sync_engine = create_engine(DATABASE_PREFIX + DATABASE_URI, connect_args={"check_same_thread": False})
local_session = sessionmaker(autocommit=False, autoflush=False, bind=sync_engine)

from src.app.core.db.database import Base
from src.app.modules.tenants.models import Tenant
from src.app.modules.users.models import User
from src.app.modules.auth.models import PasswordResetToken, EmailVerificationToken
from src.app.modules.posts.models import Post
from src.app.modules.audit_logs.models import AuditLog
from src.app.modules.files.models import File
from src.app.modules.notifications.models import Notification
from src.app.modules.rbac.models import Role, Permission, UserRole, RolePermission
from src.app.modules.storage_settings.models import StorageSettings
from src.app.modules.webhooks.models import WebhookEndpoint, WebhookDelivery
from src.app.modules.feature_flags.models import FeatureFlag
# Import all other models here to ensure they are registered in Base.metadata

# Import all models to ensure Base.metadata is fully populated for drop_all/create_all

fake = Faker()


@pytest.fixture(scope="session", autouse=True)
def setup_db():
    """Ensure fresh database schema for tests."""
    import os

    # Remove existing test database file if it exists
    if os.path.exists("./test.db"):
        try:
            os.remove("./test.db")
        except Exception as e:
            print(f"Warning: Could not remove test.db: {e}")

    try:
        Base.metadata.drop_all(bind=sync_engine)
    except Exception as e:
        print(f"Warning: drop_all failed: {e}")

    # Debug: Print registered tables
    print(f"DEBUG: Creating tables: {list(Base.metadata.tables.keys())}")

    Base.metadata.create_all(bind=sync_engine)
    yield

    try:
        Base.metadata.drop_all(bind=sync_engine)
    except Exception as e:
        print(f"Warning: drop_all failed at teardown: {e}")

    # Cleanup file after tests
    if os.path.exists("./test.db"):
        try:
            os.remove("./test.db")
        except Exception as e:
            print(f"Warning: Could not remove test.db at teardown: {e}")


from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

# Setup async engine for tests (same DB as sync engine)
# test_async_engine = create_async_engine(
#     settings.POSTGRES_ASYNC_PREFIX + settings.POSTGRES_URI, echo=False, pool_size=5, max_overflow=5, pool_pre_ping=True
# )
test_async_engine = create_async_engine(
    settings.SQLITE_ASYNC_PREFIX + "./test.db",
    echo=False,
    poolclass=NullPool,
    # pool_pre_ping=True, # Not needed with NullPool usually, but harmless
)
TestingSessionLocal = async_sessionmaker(bind=test_async_engine, class_=AsyncSession, expire_on_commit=False)


async def override_get_db() -> Generator[AsyncSession, None, None]:
    """Override for async_get_db dependency using test async engine."""
    async with TestingSessionLocal() as session:
        yield session


@pytest.fixture
def full_mock_context() -> Generator[None, None, None]:
    """Setup mock context including overrides and patches."""
    # Create valid fake data
    fake_tenant = Mock()
    fake_tenant.id = 1
    fake_tenant.slug = "test-tenant-integration"
    fake_tenant.is_active = True

    fake_user = Mock()
    fake_user.id = 1
    fake_user.username = "testuser"
    fake_user.email = "test@example.com"
    fake_user.is_superuser = False
    fake_user.tier_id = "test-tier-id"
    fake_user.name = "Test User"
    fake_user.__getitem__ = lambda self, key: getattr(self, key)
    fake_user.get = lambda key, default=None: getattr(fake_user, key, default)

    from src.app.api.dependencies import get_current_tenant, get_current_user, get_optional_user
    from src.app.core.db.database import async_get_db

    # Apply Overrides
    app.dependency_overrides[get_current_user] = lambda: fake_user
    app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
    app.dependency_overrides[get_optional_user] = lambda: fake_user
    app.dependency_overrides[async_get_db] = override_get_db

    # Create fake notification
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

    # Apply Patches
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
        patch("src.app.core.db.database.async_get_db", side_effect=override_get_db),
    ):
        yield

    # Cleanup overrides
    app.dependency_overrides = {}


@pytest.fixture
def client(full_mock_context) -> Generator[TestClient, Any, None]:
    """Sync TestClient reusing full_mock_context."""
    with TestClient(app) as _client:
        yield _client


@pytest.fixture
def db() -> Generator[Session, Any, None]:
    session = local_session()
    yield session
    session.close()


@pytest.fixture
def client_auth() -> Generator[TestClient, Any, None]:
    """Client fixture for authentication tests - patches tenant and uses SQLite DB."""
    fake_tenant = Mock()
    fake_tenant.id = 1
    fake_tenant.slug = "test-tenant-integration"
    fake_tenant.is_active = True

    from src.app.core.db.database import async_get_db

    # Override DB to use SQLite
    app.dependency_overrides[async_get_db] = override_get_db

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
        patch("src.app.core.db.database.async_get_db", side_effect=override_get_db),
    ):
        with TestClient(app) as _client:
            yield _client

    # Cleanup
    app.dependency_overrides.pop(async_get_db, None)


def override_dependency(dependency: Callable[..., Any], mocked_response: Any) -> None:
    app.dependency_overrides[dependency] = lambda: mocked_response


@pytest.fixture
def mock_db():
    """Mock database session for unit tests."""
    return Mock(spec=AsyncSession)


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Async database session fixture."""
    async with TestingSessionLocal() as session:
        yield session


@pytest.fixture
async def async_client(full_mock_context) -> AsyncGenerator[AsyncClient, None]:
    """Async client fixture."""
    # Note: ASGITransport(app=app) handles lifespan automatically.
    # We rely on full_mock_context for dependencies/patches, but avoid Nested TestClient usage.
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Tenant-ID": "test-tenant-integration"},
    ) as ac:
        yield ac


@pytest.fixture
def superuser_token_headers(full_mock_context):
    """Headers for superuser requests (overrides get_current_user)."""
    from src.app.api.dependencies import get_current_user

    # Create superuser mock
    fake_superuser = Mock()
    fake_superuser.id = 1
    fake_superuser.is_superuser = True
    fake_superuser.username = "admin"
    fake_superuser.email = "admin@example.com"
    fake_superuser.is_active = True
    fake_superuser.__getitem__ = lambda self, key: getattr(self, key)
    fake_superuser.get = lambda key, default=None: getattr(fake_superuser, key, default)

    # Override dependency (updates existing overrides from full_mock_context)
    app.dependency_overrides[get_current_user] = lambda: fake_superuser
    return {
        "Authorization": "Bearer superuser",
        "X-Tenant-ID": "test-tenant-integration",
    }


@pytest.fixture
def regular_user_token_headers(full_mock_context):
    """Headers for regular user requests."""
    return {
        "Authorization": "Bearer regular",
        "X-Tenant-ID": "test-tenant-integration",
    }


@pytest.fixture
def auth_headers(tenant):
    """Authentication headers for tenant context."""
    return {"X-Tenant-ID": tenant.slug}


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

    async def script_load(self, script: str):
        return "mock_script_sha"

    async def evalsha(self, sha, numkeys, *keys_and_args):
        """Simulate rate limit Lua script - properly increments counter.

        Mimics the Lua script:
        local current = redis.call('INCR', KEYS[1])
        if current == 1 then
            redis.call('EXPIRE', KEYS[1], ARGV[1])
        end
        return current
        """
        key = keys_and_args[0] if keys_and_args else "default_key"
        current = self.store.get(key, 0)
        if isinstance(current, str):
            current = int(current)
        current += 1
        self.store[key] = current
        return current


@pytest.fixture(autouse=True)
def mock_redis():
    """Mock Redis client globally with in-memory state."""
    fake_redis = FakeRedis()

    with (
        patch("src.app.core.setup.create_redis_cache_pool", new=AsyncMock()),
        patch("src.app.core.setup.create_redis_queue_pool", new=AsyncMock()),
        patch("src.app.core.setup.create_redis_rate_limit_pool", new=AsyncMock()),
        patch("src.app.core.setup.close_redis_cache_pool", new=AsyncMock()),
        patch("src.app.core.setup.close_redis_queue_pool", new=AsyncMock()),
        patch("src.app.core.setup.close_redis_rate_limit_pool", new=AsyncMock()),
        patch("src.app.core.utils.cache.client", new=fake_redis),
        patch("src.app.core.utils.rate_limit.rate_limiter.client", new=fake_redis),
        patch("src.app.core.utils.rate_limit.RateLimiter.get_client", return_value=fake_redis),
        patch("src.app.core.utils.queue.pool", new=fake_redis),
    ):
        yield fake_redis


@pytest.fixture
def mock_user(current_user_dict):
    """Fixture to mock current user dependency."""
    return current_user_dict


@pytest.fixture
async def create_test_user(db_session, tenant):
    """Factory fixture to create a test user."""
    from src.app.core.security import get_password_hash
    from src.app.modules.users.models import User

    async def _create_user(email: str = "test@example.com", is_superuser: bool = False, **kwargs):
        kwargs.pop("is_active", None)
        hashed_password = await get_password_hash("password")
        user = User(
            email=email,
            username=email.split("@")[0],
            hashed_password=hashed_password,
            is_superuser=is_superuser,
            tenant_id=tenant.id,
            name="Test User",
            **kwargs,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)
        return user

    return _create_user


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
