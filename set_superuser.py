import asyncio
import os
import sys

# Add src to sys.path
sys.path.append(os.getcwd())

# Import necessary modules
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError

from src.app.core.db.database import async_session_maker
from src.app.modules.tenants.models import Tenant
from src.app.modules.users.models import User


async def main():
    try:
        async with async_session_maker() as session:
            # Get tenant first
            tenant_slug = "test-tenant-e2e"
            result = await session.execute(select(Tenant).where(Tenant.slug == tenant_slug))
            tenant = result.scalar_one_or_none()

            if not tenant:
                print(f"Tenant {tenant_slug} not found")
                return

            # Find the user
            email = "premium_e2e@example.com"
            result = await session.execute(select(User).where(User.tenant_id == tenant.id, User.email == email))
            user = result.scalar_one_or_none()

            if user:
                print(f"Setting is_superuser=True for {email} in tenant {tenant_slug}")

                # Update user
                await session.execute(update(User).where(User.id == user.id).values(is_superuser=True))
                await session.commit()
                print("User updated successfully")
            else:
                print(f"User {email} not found")
    except SQLAlchemyError as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
