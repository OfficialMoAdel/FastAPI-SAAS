import os
import uuid
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
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm.session import Session
from sqlalchemy.sql.expression import TextClause

from src.app.core.config import settings


# SQLite type compilers for PostgreSQL types
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


# Ensure encryption key is set for tests - must be a valid Fernet key
_test_encryption_key = os.getenv("TEST_ENCRYPTION_KEY")
if not _test_encryption_key:
    _test_encryption_key = Fernet.generate_key().decode()
settings.ENCRYPTION_KEY = SecretStr(_test_encryption_key)

from sqlalchemy.pool import StaticPool

from src.app.main import app

# Use in-memory SQLite for tests - fresh database each run, no data conflicts
DATABASE_URI_STR = ":memory:"

print(f"DEBUG: conftest loaded. Using SQLite: {DATABASE_URI_STR}")

sync_engine = create_engine(
    f"sqlite:///{DATABASE_URI_STR}",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
local_session = sessionmaker(autocommit=False, autoflush=False, bind=sync_engine)

# Async Engine for tests
test_async_engine = create_async_engine(
    f"sqlite+aiosqlite:///{DATABASE_URI_STR}",
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_async_engine, class_=AsyncSession)


from src.app.core.db.database import Base

# Import all models to ensure Base.metadata is fully populated for drop_all/create_all
from src.app.modules.tenants.models import Tenant
from src.app.modules.users.models import User

fake = Faker()


@pytest.fixture(scope="session", autouse=True)
def setup_db():
    """Ensure fresh database schema for tests."""
    # Dispose engines to release file locks on Windows
    sync_engine.dispose()

    try:
        Base.metadata.drop_all(bind=sync_engine)
    except Exception:
        pass

    # Re-create all tables
    Base.metadata.create_all(bind=sync_engine, checkfirst=True)

    yield

    # Cleanup file after tests
    sync_engine.dispose()
    # if os.path.exists(DATABASE_PATH):
    #     try:
    #         os.remove(DATABASE_PATH)
    #     except Exception:
    #         pass


@pytest.fixture
def db(setup_db) -> Generator[Session, Any, None]:
    session = local_session()
    yield session
    session.close()


async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
    """Override database dependency to use test async session."""
    async with TestingSessionLocal() as session:
        yield session


@pytest.fixture
def client() -> Generator[TestClient, Any, None]:
    """Sync TestClient fixture with standard mocking for general API tests."""
    fake_tenant = Mock()
    fake_tenant.id = 1
    fake_tenant.slug = "test-tenant"
    fake_tenant.is_active = True

    from src.app.api.dependencies import get_current_user
    from src.app.core.db.database import async_get_db

    # Mock superuser for auth
    fake_superuser = MockUser(
        id=1,
        username="testuser",
        email="test@example.com",
        is_superuser=True,
        is_active=True,
        tenant_id=1,
    )

    async def mock_get_current_user():
        return fake_superuser

    app.dependency_overrides[async_get_db] = override_get_db
    app.dependency_overrides[get_current_user] = mock_get_current_user

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

    app.dependency_overrides.pop(async_get_db, None)
    app.dependency_overrides.pop(get_current_user, None)


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


class MockUser:
    """Mock user object that supports both attribute and dict-like access."""

    def __init__(
        self,
        id=1,
        username="testuser",
        email="test@example.com",
        is_superuser=True,
        is_active=True,
        tenant_id=1,
        roles=None,
        permissions=None,
    ):
        self.id = id
        self.username = username
        self.email = email
        self.is_superuser = is_superuser
        self.is_active = is_active
        self.tenant_id = tenant_id
        self.roles = roles or []
        # Default permissions for superuser - all common permissions
        self.permissions = (
            permissions
            if permissions is not None
            else [
                "MANAGE_STORAGE_SETTINGS",
                "MANAGE_SSO_SETTINGS",
                "MANAGE_WEBHOOKS",
                "MANAGE_USERS",
                "MANAGE_ROLES",
                "MANAGE_TENANTS",
                "MANAGE_BILLING",
                "MANAGE_API_KEYS",
                "VIEW_AUDIT_LOGS",
            ]
            if is_superuser
            else []
        )

    def __getitem__(self, key):
        return getattr(self, key)

    def get(self, key, default=None):
        return getattr(self, key, default)

    def has_permission(self, permission: str) -> bool:
        """Check if user has a specific permission."""
        return self.is_superuser or permission in self.permissions


@pytest.fixture
async def full_mock_context():
    """Full mock context for async integration tests.

    Sets up common mocks: tenant, rate limiting, cache warming, db override, auth.
    """
    fake_tenant = Mock()
    fake_tenant.id = 1
    fake_tenant.slug = "test-tenant-integration"
    fake_tenant.is_active = True

    from src.app.api.dependencies import get_current_user
    from src.app.core.db.database import async_get_db

    # Mock superuser with dict-like access for routes using current_user["id"]
    fake_superuser = MockUser(
        id=1,
        username="testuser",
        email="test@example.com",
        is_superuser=True,
        is_active=True,
        tenant_id=1,
    )

    async def mock_get_current_user():
        return fake_superuser

    app.dependency_overrides[async_get_db] = override_get_db
    app.dependency_overrides[get_current_user] = mock_get_current_user

    # PATCH RBAC dependency to respect the get_current_user override
    # This is needed because RBAC dependencies call _get_current_user_from_token directly
    # instead of using the get_current_user dependency
    import inspect

    async def mock_get_current_user_from_token(token: str, db: Any) -> dict:
        # Check if get_current_user is overridden
        override = app.dependency_overrides.get(get_current_user)
        if override:
            if inspect.iscoroutinefunction(override):
                return await override()
            return override()
        return fake_superuser

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
        patch(
            "src.app.core.rbac.dependencies._get_current_user_from_token", side_effect=mock_get_current_user_from_token
        ),
    ):
        yield

    app.dependency_overrides.pop(async_get_db, None)
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
async def unauthenticated_mock_context():
    """Mock context WITHOUT authentication for testing auth-required endpoints.

    Sets up common mocks: tenant, rate limiting, cache warming, db override.
    Does NOT mock get_current_user - allows real auth dependency to run.
    """
    fake_tenant = Mock()
    fake_tenant.id = 1
    fake_tenant.slug = "test-tenant-integration"
    fake_tenant.is_active = True

    from src.app.core.db.database import async_get_db

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
        yield

    app.dependency_overrides.pop(async_get_db, None)


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Async database session fixture."""
    async with TestingSessionLocal() as session:
        yield session


@pytest.fixture
async def async_client(full_mock_context) -> AsyncGenerator[AsyncClient, None]:
    """Async client fixture."""
    # Note: ASGITransport(app=app) handles lifespan automatically.
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Tenant-ID": "test-tenant-integration"},
    ) as ac:
        yield ac


@pytest.fixture
async def unauthenticated_async_client(unauthenticated_mock_context) -> AsyncGenerator[AsyncClient, None]:
    """Async client WITHOUT authentication for testing auth-required endpoints.

    Use this fixture when testing that endpoints correctly reject unauthenticated requests.
    """
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Tenant-ID": "test-tenant-integration"},
    ) as ac:
        yield ac


@pytest.fixture
async def superuser_token_headers(full_mock_context):
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
async def regular_user_token_headers(full_mock_context):
    """Headers for regular user requests (non-superuser)."""
    from src.app.api.dependencies import get_current_user

    # Create regular user (NOT superuser)
    fake_regular_user = MockUser(
        id=2,
        username="regular_user",
        email="user@example.com",
        is_superuser=False,
        is_active=True,
        tenant_id=1,
    )

    # Override dependency to return regular user
    app.dependency_overrides[get_current_user] = lambda: fake_regular_user
    return {
        "Authorization": "Bearer regular",
        "X-Tenant-ID": "test-tenant-integration",
    }


@pytest.fixture
async def auth_headers(tenant, superuser_token_headers):
    """Authentication headers for tenant context with valid auth token."""
    return {
        **superuser_token_headers,
        "X-Tenant-ID": tenant.slug,
    }


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
        patch("src.app.core.setup.close_redis_cache_pool", new=AsyncMock()),
        patch("src.app.core.setup.close_redis_queue_pool", new=AsyncMock()),
        patch("src.app.core.utils.cache.client", new=fake_redis),
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
def mock_sso_settings():
    """Fixture to mock SSO settings for Apple provider."""

    patches = []

    def _mock_sso_settings(provider: str, enabled: bool = True):
        patcher = patch(
            "src.app.modules.sso_settings.service.sso_settings_service.get_decrypted_settings",
            new_callable=AsyncMock,
        )
        mock = patcher.start()
        patches.append(patcher)

        mock.return_value = {
            "client_id": "com.example.app",
            "client_secret": "test-secret",
            "team_id": "TEAMID1234",
            "key_id": "KEYID12345",
            "private_key": "-----BEGIN PRIVATE KEY-----\ntest\n-----END PRIVATE KEY-----",
            "is_enabled": enabled,
        }
        return mock

    yield _mock_sso_settings

    for patcher in patches:
        patcher.stop()


@pytest.fixture
def mock_apple_oauth():
    """Fixture to mock Apple OAuth responses."""

    patches = []

    def _mock_apple_oauth(token_response: dict = None):
        # We mock the provider instance returned by _get_provider_instance
        # This is safer than patching methods on the class

        mock_provider = AsyncMock()
        mock_provider.exchange_code_for_token.return_value = token_response or {
            "access_token": "mock-access-token",
            "id_token": "mock-id-token",
            "refresh_token": "mock-refresh-token",
            "expires_in": 3600,
            "token_type": "bearer",
        }

        # Default user info setup
        # We can't import AppleUserInfo here easily if it depends on other things,
        # so we mock the return value object
        mock_user_info = Mock()
        mock_user_info.provider_user_id = "test.apple.id"
        mock_user_info.email = "test@example.com"
        mock_user_info.email_verified = True
        mock_user_info.name = "Test User"
        mock_user_info.is_private_email = False

        mock_provider.get_user_info.return_value = mock_user_info

        # Patch the factory method in service
        patcher = patch("src.app.modules.sso.service.SSOService._get_provider_instance", return_value=mock_provider)
        patcher.start()
        patches.append(patcher)

        return mock_provider

    yield _mock_apple_oauth

    for patcher in patches:
        patcher.stop()


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
