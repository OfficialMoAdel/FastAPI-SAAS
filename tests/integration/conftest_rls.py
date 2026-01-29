"""Shared pytest fixtures for RLS (Row Level Security) integration tests.

Provides reusable fixtures for RLS testing including:
- Test tenants and users
- Test data (posts) for each tenant
- RLS context management helpers
- RLS bypass helpers for setup operations
"""

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.modules.posts.models import Post
from src.app.modules.tenants.models import Tenant
from src.app.modules.users.models import User


@pytest.fixture
async def test_tenants(
    db_session: AsyncSession,
) -> AsyncGenerator[tuple[Tenant, Tenant], None]:
    """Create two test tenants for isolation testing.

    Returns:
        Tuple of (tenant_a, tenant_b) for testing cross-tenant isolation

    Cleanup:
        Automatically removes tenants after test
    """
    tenant_a = Tenant(
        name="Test Tenant A",
        slug="test-tenant-a",
        is_active=True,
    )
    tenant_b = Tenant(
        name="Test Tenant B",
        slug="test-tenant-b",
        is_active=True,
    )

    db_session.add_all([tenant_a, tenant_b])
    await db_session.commit()
    await db_session.refresh(tenant_a)
    await db_session.refresh(tenant_b)

    yield tenant_a, tenant_b

    # Cleanup
    await db_session.delete(tenant_a)
    await db_session.delete(tenant_b)
    await db_session.commit()


@pytest.fixture
async def test_users(
    db_session: AsyncSession, test_tenants: tuple[Tenant, Tenant]
) -> AsyncGenerator[tuple[User, User], None]:
    """Create test users for each tenant.

    Args:
        db_session: Database session
        test_tenants: Tuple of test tenants from test_tenants fixture

    Returns:
        Tuple of (user_a, user_b) associated with respective tenants
    """
    tenant_a, tenant_b = test_tenants

    user_a = User(
        name="User A",
        email="user_a@test.com",
        username="user_a",
        hashed_password="hashed_password_a",
        tenant_id=tenant_a.id,
    )
    user_b = User(
        name="User B",
        email="user_b@test.com",
        username="user_b",
        hashed_password="hashed_password_b",
        tenant_id=tenant_b.id,
    )

    db_session.add_all([user_a, user_b])
    await db_session.commit()
    await db_session.refresh(user_a)
    await db_session.refresh(user_b)

    yield user_a, user_b


@pytest.fixture
async def test_posts(
    db_session: AsyncSession,
    test_tenants: tuple[Tenant, Tenant],
    test_users: tuple[User, User],
) -> AsyncGenerator[tuple[Post, Post], None]:
    """Create test posts for each tenant.

    Args:
        db_session: Database session
        test_tenants: Tuple of test tenants
        test_users: Tuple of test users

    Returns:
        Tuple of (post_a, post_b) associated with respective tenants
    """
    tenant_a, tenant_b = test_tenants
    user_a, user_b = test_users

    post_a = Post(
        title="Post by Tenant A",
        text="Content A",
        tenant_id=tenant_a.id,
        created_by_user_id=user_a.id,
    )
    post_b = Post(
        title="Post by Tenant B",
        text="Content B",
        tenant_id=tenant_b.id,
        created_by_user_id=user_b.id,
    )

    db_session.add_all([post_a, post_b])
    await db_session.commit()
    await db_session.refresh(post_a)
    await db_session.refresh(post_b)

    yield post_a, post_b


class RLSContext:
    """Helper class for managing RLS context in tests."""

    def __init__(self, session: AsyncSession):
        """Initialize RLS context helper.

        Args:
            session: Database session to use for setting context
        """
        self.session = session

    async def set(self, tenant_id: int, user_id: int | None = None) -> None:
        """Set RLS context for current connection.

        Args:
            tenant_id: Tenant ID to set as current tenant
            user_id: Optional user ID to set as current user
        """
        connection = await self.session.connection()
        await connection.execute(
            text("SET LOCAL app.current_tenant_id = :tenant_id"),
            {"tenant_id": str(tenant_id)},
        )
        if user_id:
            await connection.execute(
                text("SET LOCAL app.current_user_id = :user_id"),
                {"user_id": str(user_id)},
            )

    async def clear(self) -> None:
        """Clear RLS context from current connection."""
        connection = await self.session.connection()
        await connection.execute(text("RESET app.current_tenant_id"))
        await connection.execute(text("RESET app.current_user_id"))


@pytest.fixture
async def rls_context(db_session: AsyncSession) -> RLSContext:
    """Factory fixture to set RLS context in tests.

    Usage:
        async def test_isolation(db_session, test_posts, rls_context):
            post_a, post_b = test_posts

            # Set context to tenant A
            await rls_context.set(post_a.tenant_id)

            # Query posts
            result = await db_session.execute(select(Post))
            posts = result.scalars().all()

            # Should only see tenant A posts
            assert len(posts) == 1
            assert posts[0].id == post_a.id

            await rls_context.clear()

    Args:
        db_session: Database session

    Returns:
        RLSContext helper instance
    """
    return RLSContext(db_session)


@pytest.fixture
async def rls_bypass(db_session: AsyncSession):
    """Fixture to bypass RLS for test setup operations.

    Usage:
        async def test_with_bypass(db_session, rls_bypass):
            # All queries can access all tenant data
            result = await db_session.execute(select(Post))
            all_posts = result.scalars().all()

    Args:
        db_session: Database session

    Yields:
        None (context manager style)
    """
    connection = await db_session.connection()
    await connection.execute(text("SET LOCAL app.bypass_rls = 'true'"))

    yield

    # Cleanup
    await connection.execute(text("RESET app.bypass_rls"))
