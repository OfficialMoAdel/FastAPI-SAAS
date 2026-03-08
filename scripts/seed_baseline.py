import sys
from pathlib import Path

# Add src to path
src_path = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_path))

import asyncio
import logging
import random
import uuid

from faker import Faker
from sqlalchemy import text

# Project Imports
from app.core.db.database import async_session_maker
from app.core.security import get_password_hash

# Setup Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

fake = Faker()

# Configuration
NUM_TENANTS = 100
NUM_USERS = 1000
NUM_AUDIT_LOGS = 10000


async def seed_data():
    """Seed the database with tenants, users, and audit logs using raw SQL."""
    async with async_session_maker() as session:
        try:
            logger.info("Starting baseline data seeding...")

            # 1. Seed Tenants
            logger.info(f"Creating {NUM_TENANTS} tenants...")

            # Create tenants using raw SQL to avoid ORM mapper issues
            for i in range(NUM_TENANTS):
                tenant_name = fake.company().replace("'", "''")  # Escape single quotes
                tenant_slug = f"tenant-{i}-{fake.slug()[:30]}"

                await session.execute(
                    text("""
                        INSERT INTO tenants (name, slug, is_active, storage_usage_bytes, file_count, feature_flags, created_at, updated_at)
                        VALUES (:name, :slug, :is_active, :storage_usage_bytes, :file_count, :feature_flags, NOW(), NOW())
                        ON CONFLICT DO NOTHING
                    """),  # noqa: E501
                    {
                        "name": tenant_name,
                        "slug": tenant_slug,
                        "is_active": True,
                        "storage_usage_bytes": 0,
                        "file_count": 0,
                        "feature_flags": "{}",
                    },
                )

            # Get tenant IDs
            result = await session.execute(text("SELECT id FROM tenants"))
            tenant_ids = [row[0] for row in result.all()]
            logger.info(f"Created {len(tenant_ids)} tenants.")

            if not tenant_ids:
                logger.error("Failed to create tenants. Aborting.")
                return

            # 2. Seed Users
            logger.info(f"Creating {NUM_USERS} users...")

            # Pre-compute password hash
            hashed_password = await get_password_hash("password123")

            for i in range(NUM_USERS):
                tenant_id = random.choice(tenant_ids)
                email = f"user{i}_{fake.slug()}@example.com"
                name = fake.name().replace("'", "''")
                username = f"u{i}_{fake.slug()[:14]}"  # Ensure max 20 chars (u{i}_ is ~4-5 chars)

                await session.execute(
                    text("""
                        INSERT INTO "user" (email, hashed_password, name, username, tenant_id,
                                         is_superuser, is_email_verified, is_deleted, profile_image_url, uuid, created_at, updated_at)
                        VALUES (:email, :hashed_password, :name, :username, :tenant_id,
                                :is_superuser, :is_email_verified, :is_deleted, :profile_image_url, :uuid, NOW(), NOW())
                        ON CONFLICT DO NOTHING
                    """),  # noqa: E501
                    {
                        "email": email,
                        "hashed_password": hashed_password,
                        "name": name,
                        "username": username,
                        "tenant_id": tenant_id,
                        "is_superuser": False,
                        "is_email_verified": True,
                        "is_deleted": False,
                        "profile_image_url": "https://profileimageurl.com",
                        "uuid": str(uuid.uuid4()),
                    },
                )

            # Get user IDs
            result = await session.execute(text('SELECT id FROM "user"'))
            user_ids = [row[0] for row in result.all()]
            logger.info(f"Created {len(user_ids)} users.")

            # 3. Seed Audit Logs
            logger.info(f"Creating {NUM_AUDIT_LOGS} audit logs...")

            actions = ["user.login", "file.upload", "file.download", "user.update", "tenant.update"]
            entity_types = ["user", "file", "tenant"]

            # Insert in chunks
            chunk_size = 1000
            audit_logs_data = []

            for _ in range(NUM_AUDIT_LOGS):
                tenant_id = random.choice(tenant_ids)
                user_id = random.choice(user_ids) if random.random() > 0.1 else None

                audit_logs_data.append(
                    {
                        "tenant_id": tenant_id,
                        "user_id": user_id,
                        "action": random.choice(actions),
                        "entity_type": random.choice(entity_types),
                        "entity_id": str(fake.uuid4()),
                        "details": f'{{"description": "{fake.sentence().replace(chr(39), chr(39) + chr(39))}", "metadata": {{"ip": "{fake.ipv4()}"}}}}',  # noqa: E501
                        "ip_address": fake.ipv4(),
                        "user_agent": fake.user_agent(),
                    }
                )

                if len(audit_logs_data) >= chunk_size:
                    # Execute batch insert
                    for log_data in audit_logs_data:
                        await session.execute(
                            text("""
                                INSERT INTO audit_logs (tenant_id, user_id, action, entity_type,
                                                      entity_id, details, ip_address, user_agent, created_at)
                                VALUES (:tenant_id, :user_id, :action, :entity_type,
                                        :entity_id, CAST(:details AS JSONB), :ip_address, :user_agent, NOW())
                            """),
                            log_data,
                        )
                    audit_logs_data = []

            # Insert remaining logs
            for log_data in audit_logs_data:
                await session.execute(
                    text("""
                        INSERT INTO audit_logs (tenant_id, user_id, action, entity_type,
                                              entity_id, details, ip_address, user_agent, created_at)
                        VALUES (:tenant_id, :user_id, :action, :entity_type,
                                :entity_id, CAST(:details AS JSONB), :ip_address, :user_agent, NOW())
                    """),
                    log_data,
                )

            logger.info(f"Created {NUM_AUDIT_LOGS} audit logs.")

            await session.commit()
            logger.info("Seeding complete successfully!")

            # Print summary
            logger.info("=" * 50)
            logger.info("BASELINE DATA SUMMARY")
            logger.info("=" * 50)
            logger.info(f"Tenants: {len(tenant_ids)}")
            logger.info(f"Users: {len(user_ids)}")
            logger.info(f"Audit Logs: {NUM_AUDIT_LOGS}")

        except Exception as e:
            logger.error(f"Seeding failed: {e}")
            await session.rollback()
            raise


if __name__ == "__main__":
    asyncio.run(seed_data())
