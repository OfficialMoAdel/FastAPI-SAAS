import asyncio
import sys
import os

# Add src to sys.path
sys.path.append(os.getcwd())

# Import necessary modules
from src.app.modules.rbac.models import Role, UserRole
from src.app.modules.notifications.models import Notification
from src.app.modules.files.models import File
from src.app.modules.billing.models import UserSubscription, BillingRecord
from src.app.modules.ai.models import AIChatSession, AISettings
from src.app.modules.tiers.models import Tier
from src.app.modules.webhooks.models import WebhookEndpoint, WebhookDelivery
from src.app.modules.audit_logs.models import AuditLog
from src.app.modules.feature_flags.models import FeatureFlagOverride
from src.app.modules.storage_settings.models import StorageSettings
from src.app.modules.rate_limits.models import RateLimit
from src.app.modules.users.models import User
from src.app.modules.tenants.models import Tenant

from src.app.core.db.database import async_session_maker
from sqlalchemy import select, update

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
                await session.execute(
                    update(User)
                    .where(User.id == user.id)
                    .values(is_superuser=True)
                )
                await session.commit()
                print("User updated successfully")
            else:
                print(f"User {email} not found")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
