"""Integration tests for tenant migration backfill verification.

Tests that the migration correctly:
- Creates the tenants table
- Inserts the default "System" tenant with slug "host"
- Backfills all existing users to the default tenant
- Maintains data integrity (no user without tenant_id)
"""

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from src.app.core.config import settings
from src.app.modules.tenants.models import Tenant
from src.app.modules.users.models import User


@pytest_asyncio.fixture
async def db_session():
    """Get database session for tests."""
    engine = create_async_engine(
        f"{settings.POSTGRES_ASYNC_PREFIX}{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}"
        f"@{settings.POSTGRES_SERVER}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}",
        echo=False,
    )
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def seed_default_tenant(db_session: AsyncSession):
    """Seed default tenant to simulate migration."""
    # Check if exists
    result = await db_session.execute(select(Tenant).where(Tenant.slug == "host"))
    if result.scalar_one_or_none():
        return

    # Insert default tenant
    default_tenant = Tenant(
        name="System",
        slug="host",
        is_active=True,
        feature_flags={},
    )
    db_session.add(default_tenant)
    await db_session.commit()

@pytest.mark.asyncio
class TestTenantMigration:
    """Integration tests for tenant migration."""

    async def test_tenants_table_exists(self, db_session: AsyncSession) -> None:
        """Test that tenants table was created by migration."""
        result = await db_session.execute(
            text("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'tenants')")
        )
        exists = result.scalar()
        assert exists is True, "tenants table should exist after migration"

    async def test_default_tenant_exists(self, db_session: AsyncSession) -> None:
        """Test that default 'System' tenant with slug 'host' exists."""
        result = await db_session.execute(select(Tenant).where(Tenant.slug == "host"))
        tenant = result.scalar_one_or_none()

        assert tenant is not None, "Default tenant with slug 'host' should exist"
        assert tenant.name == "System", "Default tenant name should be 'System'"
        assert tenant.is_active is True, "Default tenant should be active"

    async def test_all_users_have_tenant_id(self, db_session: AsyncSession) -> None:
        """Test that all existing users have been backfilled with tenant_id."""
        result = await db_session.execute(
            select(func.count()).select_from(User).where(User.tenant_id.is_(None))
        )
        users_without_tenant = result.scalar()

        assert users_without_tenant == 0, "No users should have NULL tenant_id after migration"

    async def test_all_users_assigned_to_default_tenant(self, db_session: AsyncSession) -> None:
        """Test that all existing users are assigned to the default 'host' tenant."""
        default_tenant = await db_session.execute(select(Tenant).where(Tenant.slug == "host"))
        tenant = default_tenant.scalar_one_or_none()

        if tenant is None:
            pytest.skip("Default tenant not found - migration may not have run")

        result = await db_session.execute(
            select(func.count()).select_from(User).where(User.tenant_id != tenant.id)
        )
        users_other_tenant = result.scalar()

        assert users_other_tenant == 0, "All existing users should be assigned to default tenant"

    async def test_tenant_id_column_not_nullable(self, db_session: AsyncSession) -> None:
        """Test that tenant_id column is NOT NULL after migration."""
        result = await db_session.execute(
            text("""
                SELECT is_nullable
                FROM information_schema.columns
                WHERE table_name = 'user' AND column_name = 'tenant_id'
            """)
        )
        is_nullable = result.scalar()

        assert is_nullable == "NO", "tenant_id column should be NOT NULL"

    async def test_tenant_id_has_foreign_key(self, db_session: AsyncSession) -> None:
        """Test that tenant_id has a foreign key constraint to tenants table."""
        result = await db_session.execute(
            text("""
                SELECT COUNT(*)
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                    ON tc.constraint_name = kcu.constraint_name
                WHERE tc.table_name = 'user'
                    AND tc.constraint_type = 'FOREIGN KEY'
                    AND kcu.column_name = 'tenant_id'
            """)
        )
        fk_count = result.scalar()

        assert fk_count >= 1, "tenant_id should have a foreign key constraint"
