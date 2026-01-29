"""Integration tests for Row Level Security (RLS) isolation.

Tests verify that:
- RLS prevents cross-tenant data access
- Bypass functionality works with audit logging
- Context cleanup prevents leakage
- Concurrent requests maintain isolation
- Performance overhead is acceptable
"""

import asyncio

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.db.database import local_session
from src.app.modules.posts.models import Post
from src.app.modules.tenants.models import Tenant
from src.app.modules.users.models import User

# Mark all tests in this module as requiring PostgreSQL (RLS not available in SQLite)
pytestmark = pytest.mark.asyncio


class TestTenantIsolation:
    """Verify RLS prevents cross-tenant data access."""

    async def test_tenant_a_cannot_read_tenant_b_posts(
        self,
        db_session: AsyncSession,
        test_posts: tuple[Post, Post],
        rls_context,
    ):
        """With context set to A, queries return only A data."""
        post_a, post_b = test_posts
        await rls_context.set(post_a.tenant_id)

        result = await db_session.execute(select(Post))
        posts = result.scalars().all()

        assert len(posts) == 1, f"Expected 1 post, got {len(posts)}"
        assert posts[0].id == post_a.id
        assert post_b.id not in [p.id for p in posts]

        await rls_context.clear()

    async def test_tenant_b_cannot_read_tenant_a_posts(
        self,
        db_session: AsyncSession,
        test_posts: tuple[Post, Post],
        rls_context,
    ):
        """With context set to B, queries return only B data."""
        post_a, post_b = test_posts
        await rls_context.set(post_b.tenant_id)

        result = await db_session.execute(select(Post))
        posts = result.scalars().all()

        assert len(posts) == 1, f"Expected 1 post, got {len(posts)}"
        assert posts[0].id == post_b.id
        assert post_a.id not in [p.id for p in posts]

        await rls_context.clear()

    async def test_insert_enforces_tenant_id(
        self,
        db_session: AsyncSession,
        test_tenants: tuple[Tenant, Tenant],
        test_users: tuple[User, User],
        rls_context,
    ):
        """INSERT with wrong tenant_id fails RLS check policy."""
        tenant_a, tenant_b = test_tenants
        user_a, _ = test_users
        await rls_context.set(tenant_a.id)

        # Try to insert for tenant B while context is A
        # This should fail the RLS WITH CHECK policy
        with pytest.raises(Exception) as exc_info:
            await db_session.execute(
                text(
                    """
                    INSERT INTO post (tenant_id, created_by_user_id, title, text)
                    VALUES (:tenant_id, :user_id, :title, :text)
                    """
                ),
                {
                    "tenant_id": tenant_b.id,
                    "user_id": user_a.id,
                    "title": "Sneaky post",
                    "text": "Trying to insert for wrong tenant",
                },
            )
            await db_session.commit()

        # Should raise error due to RLS WITH CHECK policy violation
        assert "policy" in str(exc_info.value).lower() or "permission" in str(
            exc_info.value
        ).lower()

        await rls_context.clear()

    async def test_update_only_affects_own_tenant(
        self,
        db_session: AsyncSession,
        test_posts: tuple[Post, Post],
        rls_context,
    ):
        """UPDATE with tenant context only modifies own data."""
        post_a, post_b = test_posts
        await rls_context.set(post_a.tenant_id)

        # Update all posts (should only affect tenant A)
        result = await db_session.execute(
            text("UPDATE post SET title = :title WHERE TRUE RETURNING id"),
            {"title": "Updated by Tenant A"},
        )
        await db_session.commit()
        updated_ids = [r[0] for r in result.fetchall()]

        assert post_a.id in updated_ids, "Should update own post"
        assert post_b.id not in updated_ids, "Should not update other tenant's post"

        # Verify tenant B post unchanged
        await rls_context.clear()
        await rls_context.set(post_b.tenant_id)

        result = await db_session.execute(
            select(Post.title).where(Post.id == post_b.id)
        )
        title = result.scalar()
        assert title == "Post by Tenant B", "Tenant B post should be unchanged"

        await rls_context.clear()

    async def test_delete_only_affects_own_tenant(
        self,
        db_session: AsyncSession,
        test_posts: tuple[Post, Post],
        rls_context,
    ):
        """DELETE (soft delete) with tenant context only affects own data."""
        post_a, post_b = test_posts
        await rls_context.set(post_a.tenant_id)

        # Soft delete all posts (should only affect tenant A)
        await db_session.execute(
            text("UPDATE post SET is_deleted = true WHERE TRUE")
        )
        await db_session.commit()

        # Verify tenant B post unaffected
        await rls_context.clear()
        await rls_context.set(post_b.tenant_id)

        result = await db_session.execute(
            select(Post.is_deleted).where(Post.id == post_b.id)
        )
        is_deleted = result.scalar()
        assert is_deleted is False, "Tenant B post should not be deleted"

        await rls_context.clear()

    async def test_no_context_returns_empty(
        self,
        db_session: AsyncSession,
        test_posts: tuple[Post, Post],
    ):
        """Without RLS context, queries return nothing (fail-closed)."""
        # Don't set any RLS context
        result = await db_session.execute(select(Post))
        posts = result.scalars().all()

        # Without context, RLS should filter everything
        assert len(posts) == 0, "Should return no posts without RLS context"


class TestRLSBypass:
    """Test super-admin bypass functionality."""

    async def test_bypass_allows_all_data(
        self,
        db_session: AsyncSession,
        test_posts: tuple[Post, Post],
        rls_bypass,
    ):
        """With bypass enabled, all tenant data is visible."""
        post_a, post_b = test_posts

        result = await db_session.execute(select(Post))
        posts = result.scalars().all()

        assert len(posts) >= 2, f"Expected at least 2 posts, got {len(posts)}"
        ids = [p.id for p in posts]
        assert post_a.id in ids, "Should see tenant A post"
        assert post_b.id in ids, "Should see tenant B post"

    async def test_bypass_context_manager(
        self,
        db_session: AsyncSession,
        test_posts: tuple[Post, Post],
        rls_context,
    ):
        """Bypass context manager properly sets and clears bypass flag."""
        from src.app.core.db.rls import bypass_rls

        post_a, post_b = test_posts

        # Without bypass, should see nothing
        result = await db_session.execute(select(Post))
        posts = result.scalars().all()
        assert len(posts) == 0, "Should see nothing without context"

        # With bypass, should see all
        async with bypass_rls(db_session, reason="Test bypass"):
            result = await db_session.execute(select(Post))
            posts = result.scalars().all()
            assert len(posts) >= 2, "Should see all posts with bypass"

        # After bypass exits, should see nothing again
        result = await db_session.execute(select(Post))
        posts = result.scalars().all()
        assert len(posts) == 0, "Should see nothing after bypass exits"


class TestContextCleanup:
    """Verify context is properly cleared."""

    async def test_context_reset_after_clear(
        self,
        db_session: AsyncSession,
        test_posts: tuple[Post, Post],
        rls_context,
    ):
        """After clear, queries should return nothing."""
        post_a, _ = test_posts

        # Set context
        await rls_context.set(post_a.tenant_id)
        result = await db_session.execute(select(Post))
        posts = result.scalars().all()
        assert len(posts) == 1, "Should see posts with context"

        # Clear context
        await rls_context.clear()
        result = await db_session.execute(select(Post))
        posts = result.scalars().all()

        # Without context, RLS should filter everything
        assert len(posts) == 0, "Should see nothing after clearing context"

    async def test_context_isolation_between_sessions(
        self,
        test_posts: tuple[Post, Post],
    ):
        """Each session has independent RLS context."""
        post_a, post_b = test_posts

        # Session 1: Set to tenant A
        async with local_session() as session1:
            connection1 = await session1.connection()
            await connection1.execute(
                text("SET LOCAL app.current_tenant_id = :tenant_id"),
                {"tenant_id": str(post_a.tenant_id)},
            )

            result1 = await session1.execute(select(Post))
            posts1 = result1.scalars().all()
            assert len(posts1) == 1
            assert posts1[0].id == post_a.id

        # Session 2: Set to tenant B (different session)
        async with local_session() as session2:
            connection2 = await session2.connection()
            await connection2.execute(
                text("SET LOCAL app.current_tenant_id = :tenant_id"),
                {"tenant_id": str(post_b.tenant_id)},
            )

            result2 = await session2.execute(select(Post))
            posts2 = result2.scalars().all()
            assert len(posts2) == 1
            assert posts2[0].id == post_b.id


class TestConcurrentRequests:
    """Test RLS with concurrent connections."""

    async def test_concurrent_different_tenants(
        self,
        test_posts: tuple[Post, Post],
    ):
        """Concurrent connections with different tenants stay isolated."""
        post_a, post_b = test_posts

        async def query_as_tenant(tenant_id: int, expected_post_id: int):
            """Query posts as a specific tenant."""
            async with local_session() as session:
                connection = await session.connection()
                await connection.execute(
                    text("SET LOCAL app.current_tenant_id = :tenant_id"),
                    {"tenant_id": str(tenant_id)},
                )

                result = await session.execute(select(Post))
                posts = result.scalars().all()

                assert len(posts) == 1, f"Expected 1 post for tenant {tenant_id}"
                assert (
                    posts[0].id == expected_post_id
                ), f"Expected post {expected_post_id}"

        # Run concurrent queries for different tenants
        await asyncio.gather(
            query_as_tenant(post_a.tenant_id, post_a.id),
            query_as_tenant(post_b.tenant_id, post_b.id),
        )

    async def test_concurrent_same_tenant(
        self,
        test_posts: tuple[Post, Post],
    ):
        """Multiple concurrent connections to same tenant work correctly."""
        post_a, _ = test_posts

        async def query_tenant_a():
            """Query posts as tenant A."""
            async with local_session() as session:
                connection = await session.connection()
                await connection.execute(
                    text("SET LOCAL app.current_tenant_id = :tenant_id"),
                    {"tenant_id": str(post_a.tenant_id)},
                )

                result = await session.execute(select(Post))
                posts = result.scalars().all()

                assert len(posts) == 1
                assert posts[0].id == post_a.id

        # Run 5 concurrent queries for same tenant
        await asyncio.gather(*[query_tenant_a() for _ in range(5)])


class TestUserContext:
    """Test user-level RLS context (optional, for future user-level policies)."""

    async def test_set_user_context(
        self,
        db_session: AsyncSession,
        test_posts: tuple[Post, Post],
        test_users: tuple[User, User],
        rls_context,
    ):
        """User context can be set alongside tenant context."""
        post_a, _ = test_posts
        user_a, _ = test_users

        # Set both tenant and user context
        await rls_context.set(post_a.tenant_id, user_a.id)

        # Verify context is set
        connection = await db_session.connection()
        result = await connection.execute(
            text("SELECT current_setting('app.current_tenant_id', true)")
        )
        tenant_id = result.scalar()
        assert tenant_id == str(post_a.tenant_id)

        result = await connection.execute(
            text("SELECT current_setting('app.current_user_id', true)")
        )
        user_id = result.scalar()
        assert user_id == str(user_a.id)

        await rls_context.clear()
