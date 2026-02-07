import sys
from pathlib import Path

# Add src to path
src_path = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_path))

import asyncio
import uuid

from sqlalchemy import text

# Project Imports
from app.core.db.database import async_session_maker
from app.core.security import get_password_hash


async def setup_test_data():
    """Create test tenant and user for locust tests."""
    async with async_session_maker() as session:
        try:
            print("Setting up test data for locust...")

            # 1. Create test tenant
            await session.execute(
                text("""
                    INSERT INTO tenants (name, slug, is_active, storage_usage_bytes, file_count, feature_flags, created_at, updated_at)
                    VALUES (:name, :slug, :is_active, :storage_usage_bytes, :file_count, :feature_flags, NOW(), NOW())
                    ON CONFLICT (slug) DO UPDATE SET is_active = TRUE
                """),
                {
                    "name": "Test Tenant E2E",
                    "slug": "test-tenant-e2e",
                    "is_active": True,
                    "storage_usage_bytes": 0,
                    "file_count": 0,
                    "feature_flags": "{}",
                },
            )
            print("Created test tenant: test-tenant-e2e")

            # Get tenant ID
            result = await session.execute(
                text("SELECT id FROM tenants WHERE slug = :slug"), {"slug": "test-tenant-e2e"}
            )
            tenant_id = result.scalar()

            # 2. Create test user
            # First check if user exists
            result = await session.execute(
                text('SELECT id FROM "user" WHERE email = :email'), {"email": "premium_e2e@example.com"}
            )
            existing_user = result.scalar()

            if existing_user:
                print("Test user already exists: premium_e2e@example.com")
            else:
                hashed_password = await get_password_hash("Str1ngst!")
                await session.execute(
                    text("""
                        INSERT INTO "user" (email, hashed_password, name, username, tenant_id,
                                         is_superuser, is_email_verified, is_deleted, profile_image_url, uuid, created_at, updated_at)
                        VALUES (:email, :hashed_password, :name, :username, :tenant_id,
                                :is_superuser, :is_email_verified, :is_deleted, :profile_image_url, :uuid, NOW(), NOW())
                    """),
                    {
                        "email": "premium_e2e@example.com",
                        "hashed_password": hashed_password,
                        "name": "Test User E2E",
                        "username": "test_e2e_user",
                        "tenant_id": tenant_id,
                        "is_superuser": True,
                        "is_email_verified": True,
                        "is_deleted": False,
                        "profile_image_url": "https://profileimageurl.com",
                        "uuid": str(uuid.uuid4()),
                    },
                )
                print("Created test user: premium_e2e@example.com")

            await session.commit()
            print("Test data setup complete!")

        except Exception as e:
            print(f"Setup failed: {e}")
            await session.rollback()
            raise


if __name__ == "__main__":
    asyncio.run(setup_test_data())
