"""RLS Isolation Verification Script.

Manual verification script to ensure Row Level Security is working correctly.
Run this after applying RLS migration to verify tenant isolation.

Usage:
    uv run python scripts/verify_rls_isolation.py
"""

import asyncio
import sys

from sqlalchemy import select, text

from src.app.core.db.database import local_session
from src.app.modules.posts.models import Post
from src.app.modules.tenants.models import Tenant
from src.app.modules.users.models import User

# Test results tracking
passed_tests = 0
failed_tests = 0
total_tests = 0


def test_pass(message: str) -> None:
    """Mark test as passed."""
    global passed_tests, total_tests
    passed_tests += 1
    total_tests += 1
    print(f"[PASS] {message}")


def test_fail(message: str, error: str = "") -> None:
    """Mark test as failed."""
    global failed_tests, total_tests
    failed_tests += 1
    total_tests += 1
    print(f"[FAIL] {message}")
    if error:
        print(f"  Error: {error}")


async def verify_rls_enabled(table_name: str) -> bool:
    """Verify RLS is enabled on a table."""
    async with local_session() as session:
        result = await session.execute(
            text(
                """
                SELECT relrowsecurity
                FROM pg_class
                WHERE relname = :table_name
                """
            ),
            {"table_name": table_name},
        )
        row = result.fetchone()
        return bool(row[0]) if row else False


async def verify_rls_policies(table_name: str) -> list[str]:
    """Get list of RLS policies on a table."""
    async with local_session() as session:
        result = await session.execute(
            text(
                """
                SELECT policyname
                FROM pg_policies
                WHERE tablename = :table_name
                """
            ),
            {"table_name": table_name},
        )
        return [row[0] for row in result.fetchall()]


async def test_rls_configuration():
    """Test 1: Verify RLS is enabled on expected tables."""
    print("\n=== Testing RLS Configuration ===")

    tables = ["post", "user", "tenants"]
    for table in tables:
        if await verify_rls_enabled(table):
            test_pass(f"Table '{table}' has RLS enabled")
        else:
            test_fail(f"Table '{table}' does NOT have RLS enabled")

    # Verify policies exist
    policies = await verify_rls_policies("post")
    if len(policies) > 0:
        test_pass(f"Found {len(policies)} RLS policies on 'post' table")
        for policy in policies:
            print(f"  Policy: {policy}")
    else:
        test_fail("No RLS policies found on 'post' table")


async def test_tenant_isolation():
    """Test 2: Verify tenant isolation works."""
    print("\n=== Testing Tenant Isolation ===")

    async with local_session() as session:
        # Create test tenants
        tenant_a = Tenant(
            name="Test Tenant A (Verify)",
            slug="test-verify-a",
            is_active=True,
        )
        tenant_b = Tenant(
            name="Test Tenant B (Verify)",
            slug="test-verify-b",
            is_active=True,
        )
        session.add_all([tenant_a, tenant_b])
        await session.commit()
        await session.refresh(tenant_a)
        await session.refresh(tenant_b)

        # Create users
        user_a = User(
            name="Verify User A",
            email="verify_a@test.com",
            username="verify_a",
            hashed_password="hashed",
            tenant_id=tenant_a.id,
        )
        user_b = User(
            name="Verify User B",
            email="verify_b@test.com",
            username="verify_b",
            hashed_password="hashed",
            tenant_id=tenant_b.id,
        )
        session.add_all([user_a, user_b])
        await session.commit()
        await session.refresh(user_a)
        await session.refresh(user_b)

        # Create posts for each tenant
        post_a = Post(
            title="Verify Post A",
            text="Content from Tenant A",
            tenant_id=tenant_a.id,
            created_by_user_id=user_a.id,
        )
        post_b = Post(
            title="Verify Post B",
            text="Content from Tenant B",
            tenant_id=tenant_b.id,
            created_by_user_id=user_b.id,
        )
        session.add_all([post_a, post_b])
        await session.commit()
        await session.refresh(post_a)
        await session.refresh(post_b)

        # Test isolation: Set context to tenant A
        connection = await session.connection()
        await connection.execute(
            text("SET LOCAL app.current_tenant_id = :tenant_id"),
            {"tenant_id": str(tenant_a.id)},
        )

        # Should only see tenant A posts
        result = await session.execute(select(Post))
        posts = result.scalars().all()

        if len(posts) == 1 and posts[0].id == post_a.id:
            test_pass("Tenant A can only see own posts")
        else:
            test_fail(f"Tenant A isolation failed: expected 1 post (ID {post_a.id}), got {len(posts)} posts")

        # Reset and set context to tenant B
        await connection.execute(text("RESET app.current_tenant_id"))
        await connection.execute(
            text("SET LOCAL app.current_tenant_id = :tenant_id"),
            {"tenant_id": str(tenant_b.id)},
        )

        # Should only see tenant B posts
        result = await session.execute(select(Post))
        posts = result.scalars().all()

        if len(posts) == 1 and posts[0].id == post_b.id:
            test_pass("Tenant B can only see own posts")
        else:
            test_fail(f"Tenant B isolation failed: expected 1 post (ID {post_b.id}), got {len(posts)} posts")

        # Test INSERT with wrong tenant_id (should fail)
        await connection.execute(
            text("SET LOCAL app.current_tenant_id = :tenant_id"),
            {"tenant_id": str(tenant_a.id)},
        )

        try:
            await session.execute(
                text(
                    """
                    INSERT INTO post (tenant_id, created_by_user_id, title, text)
                    VALUES (:tenant_id, :user_id, :title, :text)
                    """
                ),
                {
                    "tenant_id": tenant_b.id,
                    "user_id": user_a.id,
                    "title": "Sneaky",
                    "text": "Should fail",
                },
            )
            await session.commit()
            test_fail("Cross-tenant INSERT should have been rejected by RLS")
        except Exception:
            await session.rollback()
            test_pass("Cross-tenant INSERT correctly rejected by RLS")

        # Test UPDATE (should only affect own tenant)
        await connection.execute(
            text("SET LOCAL app.current_tenant_id = :tenant_id"),
            {"tenant_id": str(tenant_a.id)},
        )

        result = await session.execute(
            text("UPDATE post SET title = :title WHERE TRUE RETURNING id"),
            {"title": "Updated by A"},
        )
        updated_ids = [r[0] for r in result.fetchall()]

        if post_a.id in updated_ids and post_b.id not in updated_ids:
            test_pass("UPDATE only affects own tenant")
        else:
            test_fail("UPDATE affected wrong tenant's data")

        # Cleanup
        await connection.execute(text("RESET app.current_tenant_id"))
        await connection.execute(text("SET LOCAL app.bypass_rls = 'true'"))

        await session.delete(post_a)
        await session.delete(post_b)
        await session.delete(user_a)
        await session.delete(user_b)
        await session.delete(tenant_a)
        await session.delete(tenant_b)
        await session.commit()


async def test_bypass_functionality():
    """Test 3: Verify RLS bypass works."""
    print("\n=== Testing RLS Bypass ===")

    async with local_session() as session:
        # Create test data
        tenant_a = Tenant(
            name="Bypass Test A",
            slug="bypass-test-a",
            is_active=True,
        )
        tenant_b = Tenant(
            name="Bypass Test B",
            slug="bypass-test-b",
            is_active=True,
        )
        session.add_all([tenant_a, tenant_b])
        await session.commit()
        await session.refresh(tenant_a)
        await session.refresh(tenant_b)

        # Without bypass and without context, should see nothing
        result = await session.execute(select(Tenant))
        tenants = result.scalars().all()

        if len(tenants) == 0:
            test_pass("Without bypass and context, queries return nothing")
        else:
            test_fail(f"Expected 0 tenants without context, got {len(tenants)}")

        # With bypass, should see all tenants
        connection = await session.connection()
        await connection.execute(text("SET LOCAL app.bypass_rls = 'true'"))

        result = await session.execute(select(Tenant))
        tenants = result.scalars().all()

        if len(tenants) >= 2:
            test_pass(f"With bypass, can see all tenants ({len(tenants)} total)")
        else:
            test_fail(f"Bypass failed: expected at least 2 tenants, got {len(tenants)}")

        # Cleanup
        await session.delete(tenant_a)
        await session.delete(tenant_b)
        await session.commit()


async def test_no_context_behavior():
    """Test 4: Verify fail-closed behavior without context."""
    print("\n=== Testing Fail-Closed Behavior ===")

    async with local_session() as session:
        # Without setting any context, queries should return nothing
        result = await session.execute(select(Post))
        posts = result.scalars().all()

        if len(posts) == 0:
            test_pass("Without RLS context, queries return nothing (fail-closed)")
        else:
            test_fail(f"Fail-closed check failed: expected 0 posts without context, got {len(posts)}")


async def main():
    """Run all verification tests."""
    print("=" * 60)
    print("RLS Isolation Verification Script")
    print("=" * 60)

    try:
        # Test 1: Configuration
        await test_rls_configuration()

        # Test 2: Tenant Isolation
        await test_tenant_isolation()

        # Test 3: Bypass Functionality
        await test_bypass_functionality()

        # Test 4: Fail-Closed Behavior
        await test_no_context_behavior()

        # Summary
        print("\n" + "=" * 60)
        print("Verification Complete")
        print("=" * 60)
        print(f"Total Tests: {total_tests}")
        print(f"Passed: {passed_tests}")
        print(f"Failed: {failed_tests}")

        if failed_tests == 0:
            print("\n✅ All tests passed! RLS isolation is working correctly.")
            sys.exit(0)
        else:
            print(f"\n❌ {failed_tests} test(s) failed. Please review RLS configuration.")
            sys.exit(1)

    except Exception as e:
        print(f"\n❌ Verification script failed with error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
