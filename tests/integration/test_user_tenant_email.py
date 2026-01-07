"""Integration tests for per-tenant email uniqueness.

Tests that after migration:
- Same email can exist in different tenants
- Same email cannot exist twice in the same tenant
- Email uniqueness constraint is composite (email, tenant_id)

NOTE: These tests require PostgreSQL and are skipped when running with SQLite.
"""


import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from src.app.core.config import settings

# Skip entire module if using SQLite (conftest uses SQLite for tests by default)
_use_sqlite = settings.SQLITE_ASYNC_PREFIX is not None and "sqlite" in str(settings.SQLITE_ASYNC_PREFIX).lower()
if _use_sqlite:
    pytest.skip("These tests require PostgreSQL and cannot run on SQLite", allow_module_level=True)

from src.app.modules.tenants.models import Tenant
from src.app.modules.users.models import User


@pytest_asyncio.fixture
async def db_session():
    """Get database session for tests."""
    if _use_sqlite:
        pytest.skip("This fixture requires PostgreSQL")

    engine = create_async_engine(
        f"{settings.POSTGRES_ASYNC_PREFIX}{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}"
        f"@{settings.POSTGRES_SERVER}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}",
        echo=False,
    )
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def test_tenants(db_session: AsyncSession):
    """Create test tenants for email uniqueness tests."""
    import uuid

    # Use unique slugs to avoid conflicts with previous test runs
    unique_id = uuid.uuid4().hex[:8]
    slug1 = f"test-tenant-{unique_id}-1"
    slug2 = f"test-tenant-{unique_id}-2"

    tenant1 = Tenant(name="Tenant One", slug=slug1, is_active=True)
    tenant2 = Tenant(name="Tenant Two", slug=slug2, is_active=True)

    db_session.add(tenant1)
    db_session.add(tenant2)
    await db_session.commit()
    await db_session.refresh(tenant1)
    await db_session.refresh(tenant2)

    # Store IDs before yield to avoid lazy loading issues in teardown
    tenant1_id = tenant1.id
    tenant2_id = tenant2.id

    yield {"tenant1": tenant1, "tenant2": tenant2}

    # Cleanup: Delete test users first (FK constraint), then tenants
    await db_session.execute(
        text("DELETE FROM \"user\" WHERE tenant_id IN (:t1, :t2)"),
        {"t1": tenant1_id, "t2": tenant2_id},
    )
    await db_session.execute(
        text("DELETE FROM tenants WHERE id IN (:t1, :t2)"),
        {"t1": tenant1_id, "t2": tenant2_id},
    )
    await db_session.commit()


@pytest.mark.asyncio
class TestPerTenantEmailUniqueness:
    """Integration tests for per-tenant email uniqueness."""

    async def test_composite_unique_index_exists(self, db_session: AsyncSession) -> None:
        """Test that composite unique index on (email, tenant_id) exists."""
        result = await db_session.execute(
            text("""
                SELECT COUNT(*)
                FROM pg_indexes
                WHERE tablename = 'user'
                    AND indexdef LIKE '%email%'
                    AND indexdef LIKE '%tenant_id%'
            """)
        )
        index_count = result.scalar()

        assert index_count >= 1, "Composite unique index on (email, tenant_id) should exist"

    async def test_same_email_allowed_in_different_tenants(
        self, db_session: AsyncSession, test_tenants: dict
    ) -> None:
        """Test that the same email can be used in different tenants."""
        shared_email = "shared@example.com"

        user1 = User(
            name="User One",
            username="user-t1-shared",
            email=shared_email,
            hashed_password="hashed_password_1",
            tenant_id=test_tenants["tenant1"].id,
        )

        user2 = User(
            name="User Two",
            username="user-t2-shared",
            email=shared_email,
            hashed_password="hashed_password_2",
            tenant_id=test_tenants["tenant2"].id,
        )

        db_session.add(user1)
        db_session.add(user2)

        # Should not raise - same email in different tenants is allowed
        await db_session.commit()

        # Verify both users exist
        result = await db_session.execute(
            select(User).where(User.email == shared_email)
        )
        users = result.scalars().all()

        assert len(users) == 2, "Same email should be allowed in different tenants"

    async def test_duplicate_email_rejected_in_same_tenant(
        self, db_session: AsyncSession, test_tenants: dict
    ) -> None:
        """Test that duplicate email in the same tenant is rejected."""
        duplicate_email = "duplicate@example.com"

        user1 = User(
            name="First User",
            username="user-dup-1",
            email=duplicate_email,
            hashed_password="hashed_password_1",
            tenant_id=test_tenants["tenant1"].id,
        )
        db_session.add(user1)
        await db_session.commit()

        user2 = User(
            name="Second User",
            username="user-dup-2",
            email=duplicate_email,
            hashed_password="hashed_password_2",
            tenant_id=test_tenants["tenant1"].id,
        )
        db_session.add(user2)

        with pytest.raises(IntegrityError):
            await db_session.commit()

        # Rollback for cleanup
        await db_session.rollback()

    async def test_old_global_email_unique_constraint_removed(
        self, db_session: AsyncSession
    ) -> None:
        """Test that the old global email unique constraint was removed."""
        result = await db_session.execute(
            text("""
                SELECT COUNT(*)
                FROM pg_indexes
                WHERE tablename = 'user'
                    AND indexname LIKE '%email%'
                    AND indexdef NOT LIKE '%tenant_id%'
                    AND indexdef LIKE '%UNIQUE%'
            """)
        )
        old_index_count = result.scalar()

        # There should be no unique index on email alone
        assert old_index_count == 0, "Old global unique constraint on email should be removed"
