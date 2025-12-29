from collections.abc import Callable, Generator
from typing import Any
from unittest.mock import AsyncMock, Mock

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
# Import all models to ensure Base.metadata is fully populated for drop_all/create_all
from src.app.modules.users.models import User
from src.app.modules.tenants.models import Tenant
from src.app.modules.rbac.models import Role, Permission, UserRole, RolePermission
from src.app.modules.audit_logs.models import AuditLog
from src.app.modules.notifications.models import Notification, EmailTemplateOverride
from src.app.modules.webhooks.models import WebhookEndpoint, WebhookDelivery
from src.app.modules.feature_flags.models import FeatureFlag, FeatureFlagOverride
from src.app.modules.posts.models import Post
from src.app.modules.tiers.models import Tier
from src.app.modules.rate_limits.models import RateLimit

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


@pytest.fixture
def mock_redis():
    """Mock Redis connection for unit tests."""
    mock_redis = Mock()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.set = AsyncMock(return_value=True)
    mock_redis.delete = AsyncMock(return_value=True)
    return mock_redis


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
    from src.app.modules.tenants.models import Tenant
    
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
